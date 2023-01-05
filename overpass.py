from datetime import datetime, timedelta

import requests
import xmltodict

from utils import ensure_iterable


def get_changeset_adiff(changeset: dict) -> str:
    created_at = changeset['osm']['changeset']['@created_at']
    closed_at = changeset['osm']['changeset']['@closed_at']

    date_format = '%Y-%m-%dT%H:%M:%SZ'
    created_at_minus_one = (datetime.strptime(created_at, date_format) - timedelta(seconds=1)).strftime(date_format)

    return f'"{created_at_minus_one}","{closed_at}"'


def get_changeset_ids(changeset: dict) -> dict:
    result = {
        'node': [],
        'way': [],
        'relation': []
    }

    for action_type, affected_elements in changeset['osmChange'].items():
        if action_type.startswith('@'):
            continue

        for affected_elements_type, affected_elements_list in affected_elements.items():
            result[affected_elements_type] += [el['@id'] for el in affected_elements_list]

    return result


def build_query_by_ids(element_ids: dict) -> str:
    result = '('

    for element_type, element_ids in element_ids.items():
        if text_ids := ','.join(element_ids):
            result += f'{element_type}(id:{text_ids});'

    return result + ');'


class Overpass:
    def __init__(self):
        self.base_url = 'https://overpass-api.de/api/interpreter'

    def get_changeset_diff(self, changeset: dict) -> dict:
        adiff = get_changeset_adiff(changeset)
        element_ids = get_changeset_ids(changeset)

        resp = requests.post(self.base_url, data={
            'data': f'[adiff:{adiff}];{build_query_by_ids(element_ids)}out meta;'
        }, timeout=180)
        resp.raise_for_status()

        diff = xmltodict.parse(resp.text)
        current_map = self.get_current_state(element_ids)

        result = {
            'node': [],
            'way': [],
            'relation': []
        }

        for action in ensure_iterable(diff['osm']['action']):
            if action['@type'] == 'create':
                element_old = None
                element_type, element_new = next((k, v) for k, v in action.items() if not k.startswith('@'))
                element_id = element_new['@id']
            elif action['@type'] == 'modify':
                element_type, element_old = next(iter(action['old'].items()))
                element_new = next(iter(action['new'].values()))
                element_id = element_new['@id']
            elif action['@type'] == 'delete':
                element_type, element_old = next((k, v) for k, v in action.items() if not k.startswith('@'))
                element_new = None
                element_id = element_old['@id']
            else:
                raise

            element_latest = current_map[element_type].get(element_id, None)
            result[element_type].append((element_old, element_new, element_latest))

        return result

    def get_current_state(self, element_ids: dict) -> dict:
        resp = requests.post(self.base_url, data={
            'data': f'{build_query_by_ids(element_ids)}out meta;'
        }, timeout=180)
        resp.raise_for_status()

        current = xmltodict.parse(resp.text)
        result = {
            'node': {},
            'way': {},
            'relation': {}
        }

        for element_type in ['node', 'way', 'relation']:
            if element_type not in current['osm']:
                continue

            for element in ensure_iterable(current['osm'][element_type]):
                result[element_type][element['@id']] = element

        return result
