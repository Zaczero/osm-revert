import os

import xmltodict
from authlib.integrations.requests_client import OAuth1Auth

from config import CREATED_BY, NO_TAG_PREFIX, TAG_MAX_LENGTH, TAG_PREFIX
from utils import ensure_iterable, get_http_client


def sort_relations_for_osm_change(relations: list[dict]) -> list[dict]:
    change_ids = {rel['@id'] for rel in relations}

    dependency_state = [
        (rel, set(
            m['@ref'] for m in ensure_iterable(rel.get('member', []))
            if m['@type'] == 'relation'
        ).intersection(change_ids))
        for rel in relations]

    no_dependencies = [
        rel for rel, deps in dependency_state
        if not deps]

    visible = []
    hidden = []

    while no_dependencies:
        rel = no_dependencies.pop()
        rel_id = rel['@id']

        if rel['@visible'] == 'true':
            visible.append(rel)
        else:
            hidden.append(rel)

        for other_rel, deps in dependency_state:
            if rel_id in deps:
                deps.remove(rel_id)

                if not deps:
                    no_dependencies.append(other_rel)

    # delete relations with most dependencies first
    visible.extend(reversed(hidden))

    if len(visible) != len(dependency_state):
        raise ValueError('Circular relation dependencies detected')

    return visible


def build_osm_change(diff: dict, changeset_id: str | None) -> dict:
    result = {'osmChange': {
        '@version': 0.6,
        '@generator': CREATED_BY,
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
            if changeset_id:
                element['@changeset'] = changeset_id
            else:
                del element['@changeset']

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
        self.base_url_no_version = 'https://api.openstreetmap.org/api'
        self.base_url = self.base_url_no_version + '/0.6'

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

    def get_changeset_max_size(self) -> int:
        with get_http_client() as c:
            resp = c.get(f'{self.base_url_no_version}/capabilities')
            resp.raise_for_status()

        caps = xmltodict.parse(resp.text)

        return int(caps['osm']['api']['changesets']['@maximum_elements'])

    # def get_element(self, element_type: str, element_id: str, version: str | int) -> dict:
    #     with get_http_client() as c:
    #         resp = c.get(f'{self.base_url}/{element_type}/{element_id}/{version}')
    #         resp.raise_for_status()
    #
    #     return xmltodict.parse(resp.text)['osm'][element_type]

    def get_authorized_user(self) -> dict:
        with get_http_client(auth=self.auth) as c:
            resp = c.get(f'{self.base_url}/user/details.json')
            resp.raise_for_status()

        return resp.json()['user']

    def get_changeset(self, changeset_id: int) -> dict:
        with get_http_client() as c:
            info_resp = c.get(f'{self.base_url}/changeset/{changeset_id}')
            info_resp.raise_for_status()

            diff_resp = c.get(f'{self.base_url}/changeset/{changeset_id}/download')
            diff_resp.raise_for_status()

        info = xmltodict.parse(info_resp.text)
        diff = xmltodict.parse(diff_resp.text)
        diff['partition'] = {}

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

                if element['@timestamp'] not in diff['partition']:
                    diff['partition'][element['@timestamp']] = {
                        'node': [],
                        'way': [],
                        'relation': []
                    }

                diff['partition'][element['@timestamp']][element_type].append(element['@id'])

            diff['osmChange'][action_type] = new

        return info | diff

    def upload_diff(self, diff: dict, comment: str, extra_tags: dict | None = None) -> str | None:
        assert 'comment' not in extra_tags
        extra_tags['comment'] = comment

        for key, value in list(extra_tags.items()):
            assert not key.startswith(TAG_PREFIX)

            if not value:
                del extra_tags[key]
                continue

            # stringify the value
            if not isinstance(value, str):
                value = str(value)
                extra_tags[key] = value

            # add revert: prefix if applicable
            if key not in NO_TAG_PREFIX:
                del extra_tags[key]
                key = f'{TAG_PREFIX}:{key}'
                extra_tags[key] = value

            # trim value if too long
            if len(value) > TAG_MAX_LENGTH:
                print(f'🚧 Warning: Trimming {key} value because it exceeds {TAG_MAX_LENGTH} characters: {value}')
                extra_tags[key] = value[:252] + '…'

        with get_http_client(auth=self.auth, headers={'Content-Type': 'text/xml; charset=utf-8'}) as c:
            changeset = {'osm': {'changeset': {'tag': [
                {'@k': k, '@v': v} for k, v in extra_tags.items()
            ]}}}
            changeset_xml = xmltodict.unparse(changeset).encode('utf-8')

            cs_resp = c.put(f'{self.base_url}/changeset/create', data=changeset_xml)
            cs_resp.raise_for_status()
            changeset_id = cs_resp.text

            osm_change = build_osm_change(diff, changeset_id)
            osm_change_xml = xmltodict.unparse(osm_change).encode('utf-8')

            diff_resp = c.post(f'{self.base_url}/changeset/{changeset_id}/upload', data=osm_change_xml, timeout=150)

            cs_resp = c.put(f'{self.base_url}/changeset/{changeset_id}/close')
            cs_resp.raise_for_status()

        if diff_resp.status_code == 409:
            print(f'🆚 Failed to upload the changes ({diff_resp.status_code})')
            print(f'🆚 The Overpass data is outdated, please try again shortly')
            return None

        if diff_resp.status_code != 200:
            print(f'😵 Failed to upload the changes ({diff_resp.status_code})')
            print(f'😵 {diff_resp.text}')
            return None

        return changeset_id

    def add_comment_to_changeset(self, changeset_id: int, comment: str) -> None:
        with get_http_client(auth=self.auth) as c:
            c.post(f'{self.base_url}/changeset/{changeset_id}/comment', data={
                'text': comment
            })
