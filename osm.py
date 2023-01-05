import requests
import xmltodict

from utils import ensure_iterable


class OsmApi:
    def __init__(self, username: str, password: str):
        self.base_url = 'https://api.openstreetmap.org/api/0.6'
        self.auth = (username, password)

    def get_changeset(self, changeset_id: int) -> dict:
        info_resp = requests.get(f'{self.base_url}/changeset/{changeset_id}')
        info_resp.raise_for_status()
        info = xmltodict.parse(info_resp.text)

        diff_resp = requests.get(f'{self.base_url}/changeset/{changeset_id}/download')
        diff_resp.raise_for_status()
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
