from datetime import datetime, timedelta
from typing import Optional

import xmltodict
from httpx import Client

from utils import ensure_iterable


def parse_timestamp(timestamp: str) -> int:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    return int(datetime.strptime(timestamp, date_format).timestamp())


def get_changeset_adiff(changeset: dict) -> str:
    date_from = changeset['osm']['changeset']['@created_at']
    date_to = changeset['osm']['changeset']['@closed_at']

    date_format = '%Y-%m-%dT%H:%M:%SZ'
    created_at_minus_one = (datetime.strptime(date_from, date_format) - timedelta(seconds=1)).strftime(date_format)

    return f'"{created_at_minus_one}","{date_to}"'


def get_current_adiff(changeset: dict) -> str:
    date_from = changeset['osm']['changeset']['@closed_at']

    return f'"{date_from}"'


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


def get_diff_ids(diff: dict) -> dict:
    result = {}

    for element_type, elements in diff.items():
        result[element_type] = [element_id for _, element_id, _, _ in elements]

    return result


def build_query_by_ids(element_ids: dict) -> str:
    result = '('

    for element_type, element_ids in element_ids.items():
        if text_ids := ','.join(element_ids):
            result += f'{element_type}(id:{text_ids});'

    return result + ');'


def get_current_map(actions: list) -> dict:
    result = {
        'node': {},
        'way': {},
        'relation': {}
    }

    for action in ensure_iterable(actions):
        element_type, element = next(iter(action['new'].items()))
        result[element_type][element['@id']] = element

    return result


def ensure_visible_tag(element: Optional[dict]) -> None:
    if not element:
        return

    if '@visible' not in element:
        element['@visible'] = 'true'


class Overpass:
    def __init__(self):
        # TODO: self-hosted for available BBOX
        # self.base_url = 'https://overpass.monicz.pl/api/interpreter'
        self.base_url = 'https://overpass-api.de/api/interpreter'

    def get_changeset_elements_history(self, changeset: dict) -> dict:
        changeset_adiff = get_changeset_adiff(changeset)
        current_adiff = get_current_adiff(changeset)
        element_ids = get_changeset_ids(changeset)
        query_by_ids = build_query_by_ids(element_ids)

        with Client() as c:
            changeset_resp = c.post(self.base_url, data={
                'data': f'[timeout:180][adiff:{changeset_adiff}];{query_by_ids}out meta;'
            }, timeout=300)
            changeset_resp.raise_for_status()

            current_resp = c.post(self.base_url, data={
                'data': f'[timeout:180][adiff:{current_adiff}];{query_by_ids}out meta;'
            }, timeout=300)
            current_resp.raise_for_status()

        changeset_diff = xmltodict.parse(changeset_resp.text)
        current_diff = xmltodict.parse(current_resp.text)
        current_map = get_current_map(current_diff['osm']['action'])

        result = {
            'node': [],
            'way': [],
            'relation': []
        }

        for action in ensure_iterable(changeset_diff['osm']['action']):
            if action['@type'] == 'create':
                element_old = None
                element_type, element_new = next((k, v) for k, v in action.items() if not k.startswith('@'))
            elif action['@type'] in {'modify', 'delete'}:
                element_type, element_old = next(iter(action['old'].items()))
                element_new = next(iter(action['new'].values()))
            else:
                raise

            timestamp = parse_timestamp(element_new['@timestamp'])
            element_id = element_new['@id']
            element_current = current_map[element_type].get(element_id, element_new)

            ensure_visible_tag(element_old)
            ensure_visible_tag(element_new)
            ensure_visible_tag(element_current)

            result[element_type].append((timestamp, element_id, element_old, element_new, element_current))

        return result
