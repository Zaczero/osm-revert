from typing import Optional

import xmltodict
from httpx import Client

from utils import ensure_iterable


def apply_changeset_id(diff, changeset_id) -> None:
    for element_type, elements in diff.items():
        for element in elements:
            element['@changeset'] = changeset_id


class OsmApi:
    def __init__(self, username: str, password: str):
        self.base_url = 'https://api.openstreetmap.org/api/0.6'
        self.auth = (username, password)

    def get_changeset(self, changeset_id: int) -> dict:
        with Client() as c:
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

    def upload_diff(self, diff: dict, comment: str, extra_tags: Optional[dict] = None) -> bool:
        assert 'comment' not in extra_tags
        extra_tags['comment'] = comment

        with Client(
                auth=self.auth,
                headers={'Content-Type': 'text/xml; charset=utf-8'}
        ) as c:
            changeset = {'osm': {'changeset': {'tag': [
                {'@k': k, '@v': v} for k, v in extra_tags.items()
            ]}}}
            changeset_xml = xmltodict.unparse(changeset).encode('utf-8')

            cs_resp = c.put(f'{self.base_url}/changeset/create', data=changeset_xml)
            cs_resp.raise_for_status()
            changeset_id = cs_resp.text

            apply_changeset_id(diff, changeset_id)

            osm_change = {'osmChange': {
                '@version': 0.6,
                'modify': {
                    'node': [e for e in diff['node'] if e['@visible'] == 'true'],
                    'way': [e for e in diff['way'] if e['@visible'] == 'true'],
                    'relation': [e for e in diff['relation'] if e['@visible'] == 'true']
                },
                'delete': {
                    'node': [e for e in diff['node'] if e['@visible'] == 'false'],
                    'way': [e for e in diff['way'] if e['@visible'] == 'false'],
                    'relation': [e for e in diff['relation'] if e['@visible'] == 'false']
                }}}
            osm_change_xml = xmltodict.unparse(osm_change).encode('utf-8')

            diff_resp = c.post(f'{self.base_url}/changeset/{changeset_id}/upload', data=osm_change_xml)

            cs_resp = c.put(f'{self.base_url}/changeset/{changeset_id}/close')
            cs_resp.raise_for_status()

        return diff_resp.status_code == 200
