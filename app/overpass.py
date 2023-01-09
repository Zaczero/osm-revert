from datetime import datetime, timedelta
from typing import Optional

import xmltodict

from utils import ensure_iterable, get_http_client


def parse_timestamp(timestamp: str) -> int:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    return int(datetime.strptime(timestamp, date_format).timestamp())


def get_changeset_adiff(timestamp: str) -> str:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    created_at_minus_one = (datetime.strptime(timestamp, date_format) - timedelta(seconds=1)).strftime(date_format)

    return f'"{created_at_minus_one}","{timestamp}"'


def get_current_adiff(timestamp: str) -> str:
    return f'"{timestamp}"'


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


def build_query_by_ids(element_ids: dict) -> (str, int):
    query_size = 0

    result = '('

    for element_type, element_ids in element_ids.items():
        query_size += len(element_ids)

        if text_ids := ','.join(element_ids):
            result += f'{element_type}(id:{text_ids});'

    return result + ');', query_size


def build_query_parents_by_ids(element_ids: dict) -> str:
    return f'node(id:{",".join(element_ids["node"]) if element_ids["node"] else "-1"})->.n;' \
           f'way(id:{",".join(element_ids["way"]) if element_ids["way"] else "-1"})->.w;' \
           f'rel(id:{",".join(element_ids["relation"]) if element_ids["relation"] else "-1"})->.r;' \
           f'(way(bn.n);rel(bn.n);rel(bw.w);rel(br.r););'


def get_current_map(actions: list) -> dict:
    result = {
        'node': {},
        'way': {},
        'relation': {}
    }

    for action in actions:
        if action['@type'] == 'create':
            element_type, element = next(iter((k, v) for k, v in action.items() if not k.startswith('@')))
        else:
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
        self.base_urls = [
            'https://overpass.monicz.dev/api/interpreter',
            'https://overpass-api.de/api/interpreter'
        ]

    def get_changeset_elements_history(self, changeset: dict) -> Optional[dict]:
        errors = []

        for base_url in self.base_urls:
            result = self._get_changeset_elements_history(changeset, base_url)

            # everything ok
            if isinstance(result, dict):
                return result

            errors.append(result)

        # all errors are the same
        if all(errors[0] == e for e in errors[1:]):
            print(f'{errors[0]} (x{len(errors)})')
        else:
            print('❗️ Multiple issues occurred:')

            for i, error in enumerate(errors):
                print(f'[{i + 1}/{len(errors)}]: {error}')

        return None

    def _get_changeset_elements_history(self, changeset: dict, base_url: str) -> dict | str:
        changeset_action = []
        current_action = []

        with get_http_client() as c:
            for timestamp, element_ids in sorted(changeset['partition'].items(), key=lambda t: t[0]):
                changeset_adiff = get_changeset_adiff(timestamp)
                current_adiff = get_current_adiff(timestamp)
                query_by_ids, query_size = build_query_by_ids(element_ids)

                changeset_data = f'[timeout:180][adiff:{changeset_adiff}];{query_by_ids}out meta;'
                changeset_resp = c.post(base_url, data={'data': changeset_data}, timeout=300)
                changeset_resp.raise_for_status()
                changeset_diff = xmltodict.parse(changeset_resp.text)
                changeset_partition_action = ensure_iterable(changeset_diff['osm'].get('action', []))

                if len(changeset_partition_action) != query_size:
                    if parse_timestamp(changeset_diff['osm']['meta']['@osm_base']) <= parse_timestamp(timestamp):
                        return '🕒️ The Overpass data is outdated, please try again shortly'
                    else:
                        return '❓️ The Overpass data is incomplete'

                current_data = f'[timeout:180][adiff:{current_adiff}];{query_by_ids}out meta;'
                current_resp = c.post(base_url, data={'data': current_data}, timeout=300)
                current_resp.raise_for_status()
                current_diff = xmltodict.parse(current_resp.text)
                current_partition_action = ensure_iterable(current_diff['osm'].get('action', []))

                changeset_action.extend(changeset_partition_action)
                current_action.extend(current_partition_action)

        current_map = get_current_map(current_action)

        result = {
            'node': [],
            'way': [],
            'relation': []
        }

        for action in changeset_action:
            if action['@type'] == 'create':
                element_old = None
                element_type, element_new = next((k, v) for k, v in action.items() if not k.startswith('@'))
            elif action['@type'] in {'modify', 'delete'}:
                element_type, element_old = next(iter(action['old'].items()))
                element_new = next(iter(action['new'].values()))
            else:
                raise

            if element_new['@changeset'] != changeset['osm']['changeset']['@id']:
                return '❓ The Overpass data is corrupted (bad_changeset)'

            if element_old and int(element_new['@version']) - int(element_old['@version']) != 1:
                return '❓ The Overpass data is corrupted (bad_version)'

            timestamp = parse_timestamp(element_new['@timestamp'])
            element_id = element_new['@id']
            element_current = current_map[element_type].get(element_id, element_new)

            ensure_visible_tag(element_old)
            ensure_visible_tag(element_new)
            ensure_visible_tag(element_current)

            result[element_type].append((timestamp, element_id, element_old, element_new, element_current))

        return result

    def update_parents(self, invert: dict) -> int:
        base_url = self.base_urls[0]

        invert_ids = {
            'node': {e['@id'] for e in invert['node']},
            'way': {e['@id'] for e in invert['way']},
            'relation': {e['@id'] for e in invert['relation']}
        }

        deleting_ids = {
            'node': {e['@id'] for e in invert['node'] if e['@visible'] == 'false'},
            'way': {e['@id'] for e in invert['way'] if e['@visible'] == 'false'},
            'relation': {e['@id'] for e in invert['relation'] if e['@visible'] == 'false'}
        }

        if sum(len(el) for el in deleting_ids.values()) == 0:
            return 0

        query_by_ids = build_query_parents_by_ids(deleting_ids)

        with get_http_client() as c:
            parents_data = f'[timeout:180];{query_by_ids}out meta;'
            parents_resp = c.post(base_url, data={'data': parents_data}, timeout=300)
            parents_resp.raise_for_status()
            data = xmltodict.parse(parents_resp.text)

        parents = {
            'node': ensure_iterable(data['osm'].get('node', [])),
            'way': ensure_iterable(data['osm'].get('way', [])),
            'relation': ensure_iterable(data['osm'].get('relation', [])),
        }

        fixed_parents = 0

        for element_type, elements in parents.items():
            for element in elements:
                if element['@id'] in invert_ids[element_type]:
                    continue

                if element_type == 'way':
                    element['nd'] = [
                        n for n in ensure_iterable(element.get('nd', []))
                        if n['@ref'] not in deleting_ids['node']
                    ]

                    # delete single node ways
                    if len(element['nd']) == 1:
                        element['nd'] = []

                    if not element['nd']:
                        element['@visible'] = 'false'
                elif element_type == 'relation':
                    element['member'] = [
                        m for m in ensure_iterable(element.get('member', []))
                        if m['@ref'] not in deleting_ids[m['@type']]
                    ]

                    if not element['member']:
                        element['@visible'] = 'false'
                else:
                    raise

                ensure_visible_tag(element)
                invert[element_type].append(element)
                fixed_parents += 1

        return fixed_parents
