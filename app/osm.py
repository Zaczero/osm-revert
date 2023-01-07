import os
from typing import Optional

import xmltodict
from authlib.integrations.httpx_client import OAuth1Auth
from httpx import Client

from config import USER_AGENT
from utils import ensure_iterable


def sort_relations_for_osm_change(relations: list[dict]) -> list[dict]:
    change_ids = {rel['@id'] for rel in relations}

    dependencies_map = {
        rel: set(
            m['@ref'] for m in ensure_iterable(rel.get('member', []))
            if m['type'] == 'relation'
        ).intersection(change_ids)
        for rel in relations}

    no_dependencies = {
        rel for rel in relations
        if not dependencies_map[rel]}

    visible = []
    hidden = []

    while no_dependencies:
        rel = no_dependencies.pop()

        if rel['@visible'] == 'true':
            visible.append(rel)
        else:
            hidden.append(rel)

        for other_rel, deps in dependencies_map.items():
            if rel in deps:
                deps.remove(rel)

                if not deps:
                    no_dependencies.add(other_rel)

    if dependencies_map:
        raise ValueError('Circular relation dependencies detected')

    # delete relations with most dependencies first
    visible.extend(reversed(hidden))
    return visible


# TODO: deleting item should remove member from others?
def build_osm_change(diff: dict, changeset_id: str) -> dict:
    result = {'osmChange': {
        '@version': 0.6,
        'modify': {
            'node': [],
            'way': [],
            'relation': []
        },
        'delete': {
            'relation': [],
            'way': [],
            'node': []
        }}}

    for element_type, elements in diff.items():
        if element_type == 'relation':
            elements = sort_relations_for_osm_change(elements)

        for element in elements:
            element['@changeset'] = changeset_id

            if element['@visible'] == 'true':
                action = 'modify'
            else:
                action = 'delete'
                element.pop('@lat', None)
                element.pop('@lon', None)
                element.pop('tag', None)
                element.pop('nd', None)
                element.pop('member', None)

            result['osmChange'][action][element_type].append(element)

    return result


class OsmApi:
    def __init__(self, *,
                 username: str = None, password: str = None,
                 oauth_token: str = None, oauth_token_secret: str = None):
        self.base_url = 'https://api.openstreetmap.org/api/0.6'

        if oauth_token and oauth_token_secret:
            self.auth = OAuth1Auth(
                client_id=os.getenv('CONSUMER_KEY'),
                client_secret=os.getenv('CONSUMER_SECRET'),
                token=oauth_token,
                token_secret=oauth_token_secret
            )
        elif username and password:
            self.auth = (username, password)
        else:
            raise Exception('Authorization is required')

    def get_authorized_display_name(self) -> str:
        with Client(auth=self.auth, headers={'user-agent': USER_AGENT}) as c:
            resp = c.get('https://api.openstreetmap.org/api/0.6/user/details.json')
            resp.raise_for_status()

        return resp.json()['user']['display_name']

    def get_changeset(self, changeset_id: int) -> dict:
        with Client(headers={'user-agent': USER_AGENT}) as c:
            info_resp = c.get(f'{self.base_url}/changeset/{changeset_id}')
            info_resp.raise_for_status()

            diff_resp = c.get(f'{self.base_url}/changeset/{changeset_id}/download')
            diff_resp.raise_for_status()

        info = xmltodict.parse(info_resp.text)
        diff = xmltodict.parse(diff_resp.text)

        for action_type, affected_elements in diff['osmChange'].items():
            if action_type.startswith('@'):
                continue

            new = {
                'node': [],
                'way': [],
                'relation': []
            }

            for affected_element in ensure_iterable(affected_elements):
                element_type, element = next(iter(affected_element.items()))
                new[element_type].append(element)

            diff['osmChange'][action_type] = new

        return info | diff

    def upload_diff(self, diff: dict, comment: str, extra_tags: Optional[dict] = None) -> Optional[str]:
        assert 'comment' not in extra_tags
        extra_tags['comment'] = comment

        with Client(auth=self.auth, headers={'user-agent': USER_AGENT, 'content-type': 'text/xml; charset=utf-8'}) as c:
            changeset = {'osm': {'changeset': {'tag': [
                {'@k': k, '@v': v} for k, v in extra_tags.items()
            ]}}}
            changeset_xml = xmltodict.unparse(changeset).encode('utf-8')

            cs_resp = c.put(f'{self.base_url}/changeset/create', data=changeset_xml)
            cs_resp.raise_for_status()
            changeset_id = cs_resp.text

            osm_change = build_osm_change(diff, changeset_id)
            osm_change_xml = xmltodict.unparse(osm_change).encode('utf-8')

            diff_resp = c.post(f'{self.base_url}/changeset/{changeset_id}/upload', data=osm_change_xml)

            cs_resp = c.put(f'{self.base_url}/changeset/{changeset_id}/close')
            cs_resp.raise_for_status()

        if diff_resp.status_code != 200:
            print(f'😵 Failed to upload the changes ({diff_resp.status_code}): {diff_resp.text}')
            return None

        return changeset_id
