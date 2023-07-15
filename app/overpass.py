import html
import re
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import chain

import xmltodict
from requests import Session

from diff_entry import DiffEntry
from utils import ensure_iterable, get_http_client


def parse_timestamp(timestamp: str) -> int:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    return int(datetime.strptime(timestamp, date_format).timestamp())


def get_bbox(changeset: dict) -> str:
    e = changeset['osm']['changeset']
    min_lat, max_lat = e['@min_lat'], e['@max_lat']
    min_lon, max_lon = e['@min_lon'], e['@max_lon']
    return f'[bbox:{min_lat},{min_lon},{max_lat},{max_lon}]'


def get_old_date(timestamp: str) -> str:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    created_at_minus_one = (datetime.strptime(timestamp, date_format) - timedelta(seconds=1)).strftime(date_format)

    return f'[date:"{created_at_minus_one}"]'


def get_new_date(timestamp: str) -> str:
    return f'[date:"{timestamp}"]'


def get_changeset_adiff(timestamp: str) -> str:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    created_at_minus_one = (datetime.strptime(timestamp, date_format) - timedelta(seconds=1)).strftime(date_format)

    return f'[adiff:"{created_at_minus_one}","{timestamp}"]'


def get_current_adiff(timestamp: str) -> str:
    return f'[adiff:"{timestamp}"]'


def get_element_types_from_selector(selector: str) -> list[str]:
    if selector in {'node', 'way', 'relation'}:
        return [selector]

    result = []

    if 'n' in selector:
        result.append('node')
    if 'w' in selector:
        result.append('way')
    if 'r' in selector:
        result.append('relation')

    return result


def build_query_filtered(element_ids: dict, query_filter: str) -> str:
    element_ids = deepcopy(element_ids)

    # ensure valid query if no ids are present
    for invert_ids in element_ids.values():
        if not invert_ids:
            invert_ids.append('-1')

    implicit_query_way_children = bool(query_filter)

    # default everything query filter
    if not query_filter:
        query_filter = 'node;way;relation;'

    # ensure proper query ending
    if not query_filter.endswith(';'):
        query_filter += ';'

    # replace 'rel' alias with 'relation'
    for match in sorted(re.finditer(r'\brel\b', query_filter),
                        key=lambda m: m.start(),
                        reverse=True):
        start, end = match.start(), match.end()

        query_filter = query_filter[:start] + 'relation' + query_filter[end:]

    # handle custom (!id:)
    for match in sorted(re.finditer(r'\(\s*!\s*id\s*:(?P<id>(\s*(,\s*)?\d+)+)\s*\)', query_filter),
                        key=lambda m: m.start(),
                        reverse=True):
        start, end = match.start(), match.end()
        invert_ids = (i.strip() for i in match.group('id').split(',') if i.strip())
        selector = re.match(r'.*\b(nwr|nw|nr|wr|node|way|relation)\b', query_filter[:start], re.DOTALL).group(1)

        joined_new_ids = ','.join(
            set(chain.from_iterable(
                element_ids[et]
                for et in get_element_types_from_selector(selector)))
            .difference(invert_ids)
        )

        query_filter = query_filter[:start] + f'(id:{joined_new_ids})' + query_filter[end:]

    # apply element id filtering
    for match in sorted(re.finditer(r'\b(nwr|nw|nr|wr|node|way|relation)\b', query_filter),
                        key=lambda m: m.start(),
                        reverse=True):
        end = match.end()
        selector = match.group(1)

        joined_element_ids = ','.join(
            set(chain.from_iterable(
                element_ids[et]
                for et in get_element_types_from_selector(selector)))
        )

        query_filter = query_filter[:end] + f'(id:{joined_element_ids})' + query_filter[end:]

    if implicit_query_way_children:
        return f'({query_filter});' \
               f'out meta;' \
               f'node(w);' \
               f'out meta;'
    else:
        return f'({query_filter});' \
               f'out meta;'


def build_query_parents_by_ids(element_ids: dict) -> str:
    return f'node(id:{",".join(element_ids["node"]) if element_ids["node"] else "-1"})->.n;' \
           f'way(id:{",".join(element_ids["way"]) if element_ids["way"] else "-1"})->.w;' \
           f'rel(id:{",".join(element_ids["relation"]) if element_ids["relation"] else "-1"})->.r;' \
           f'(way(bn.n);rel(bn.n);rel(bw.w);rel(br.r););' \
           f'out meta;'


def fetch_overpass(client: Session, post_url: str, data: str, *, check_bad_request: bool = False) -> dict | str:
    response = client.post(post_url, data={'data': data}, timeout=300)

    if check_bad_request and response.status_code == 400:
        s = response.text.find('<body>')
        e = response.text.find('</body>')

        if e > s > -1:
            body = response.text[s + 6:e].strip()
            body = re.sub(r'<.*?>', '', body, re.DOTALL)
            lines = [html.unescape(line.strip()[7:])
                     for line in body.split('\n')
                     if line.strip().startswith('Error: ')]

            if lines:
                return '\n'.join(
                    [f'🛑 Overpass - Bad Request:'] +
                    [f'🛑 {line}' for line in lines])

    response.raise_for_status()  # TODO: return error message instead raise
    return xmltodict.parse(response.text)


def get_current_map(actions: list[dict]) -> dict[str, dict[str, dict]]:
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


# TODO: include default actions
def parse_action(action: dict) -> tuple[str, dict | None, dict]:
    if action['@type'] == 'create':
        element_old = None
        element_type, element_new = next((k, v) for k, v in action.items() if not k.startswith('@'))
    elif action['@type'] in {'modify', 'delete'}:
        element_type, element_old = next(iter(action['old'].items()))
        element_new = next(iter(action['new'].values()))
    else:
        raise

    return element_type, element_old, element_new


def ensure_visible_tag(element: dict | None) -> None:
    if not element:
        return

    if '@visible' not in element:
        element['@visible'] = 'true'


class Overpass:
    def __init__(self):
        self.base_urls = [
            'https://overpass.monicz.dev/api',
            'https://overpass-api.de/api'
        ]

    def get_changeset_elements_history(self, changeset: dict, steps: int, query_filter: str) -> dict[str, list[DiffEntry]] | None:
        errors = []

        for base_url in self.base_urls:
            if errors:
                print(f'[2/{steps}] Retrying …')

            result = self._get_changeset_elements_history(changeset, steps, query_filter, base_url)

            # everything ok
            if isinstance(result, dict):
                return result

            errors.append(result)

        # all errors are the same
        if all(errors[0] == e for e in errors[1:]):
            print(f'{errors[0]} (x{len(errors)})')
        else:
            print('❗️ Multiple errors occurred:')

            for i, error in enumerate(errors):
                print(f'[{i + 1}/{len(errors)}]: {error}')

        return None

    def _get_changeset_elements_history(self, changeset: dict, steps: int, query_filter: str, base_url: str) -> dict[str, list[DiffEntry]] | str:
        # shlink = Shlink()
        shlink_available = False  # shlink.available

        bbox = get_bbox(changeset)
        changeset_id = changeset['osm']['changeset']['@id']
        changeset_edits = []
        current_action = []

        with get_http_client() as c:
            for i, (timestamp, element_ids) in enumerate(sorted(changeset['partition'].items(), key=lambda t: t[0])):
                partition_adiff = get_changeset_adiff(timestamp)
                current_adiff = get_current_adiff(timestamp)
                query_unfiltered = build_query_filtered(element_ids, '')

                partition_query = f'[timeout:180]{bbox}{partition_adiff};{query_unfiltered}'
                partition_diff = fetch_overpass(c, base_url + '/interpreter', partition_query)
                assert isinstance(partition_diff, dict)
                partition_action = ensure_iterable(partition_diff['osm'].get('action', []))

                if parse_timestamp(partition_diff['osm']['meta']['@osm_base']) <= parse_timestamp(timestamp):
                    return '🕒️ Overpass is updating, please try again shortly'

                partition_size = len(partition_action)
                query_size = sum(len(v) for v in element_ids.values())

                if partition_size != query_size:
                    return f'❓️ Overpass data is incomplete: {partition_size} != {query_size}'

                if query_filter:
                    query_filtered = build_query_filtered(element_ids, query_filter)

                    filtered_query = f'[timeout:180]{bbox}{partition_adiff};{query_filtered}'
                    filtered_diff = fetch_overpass(c, base_url + '/interpreter', filtered_query, check_bad_request=True)

                    if isinstance(filtered_diff, str):
                        return filtered_diff

                    filtered_action = ensure_iterable(filtered_diff['osm'].get('action', []))

                    dedup_node_ids = set()
                    data_map = {
                        'node': {},
                        'way': {},
                        'relation': {}
                    }

                    for a in partition_action:
                        t, o, n = parse_action(a)
                        data_map[t][n['@id']] = (o, n)

                    for action in filtered_action:
                        element_type, element_old, element_new = parse_action(action)

                        # cleanup extra nodes
                        if element_type == 'node':
                            # nodes of filtered query elements are often unrelated (skeleton)
                            if element_new['@changeset'] != changeset_id:
                                continue

                            # the output may contain duplicate nodes due to double out …;
                            if element_new['@id'] in dedup_node_ids:
                                continue

                            dedup_node_ids.add(element_new['@id'])

                        # merge data
                        old_new_t = data_map[element_type].get(element_new['@id'], None)

                        if old_new_t is None:
                            return f'❓️ Overpass data is incomplete (missing_merge)'

                        if old_new_t[1]['@version'] != element_new['@version']:
                            return f'❓️ Overpass data is incomplete (bad_merge_version)'

                        changeset_edits.append((element_type,) + old_new_t)

                else:
                    changeset_edits.extend(parse_action(a) for a in partition_action)

                current_query = f'[timeout:180]{bbox}{current_adiff};{query_unfiltered}'
                current_diff = fetch_overpass(c, base_url + '/interpreter', current_query)
                assert isinstance(current_diff, dict)
                current_partition_action = ensure_iterable(current_diff['osm'].get('action', []))
                current_action.extend(current_partition_action)

                # BLOCKED: https://github.com/shlinkio/shlink/issues/1674
                # if shlink_available:
                #     try:
                #         query_long_url = base_url + f'/convert?data={quote_plus(changeset_data)}&target=mapql'
                #         query_short_url = shlink.shorten(query_long_url)
                #         print(f'[{i + 2}/{steps}] Partition OK ({partition_size}); Query: {query_short_url}')
                #     except Exception:
                #         traceback.print_exc()
                #         shlink_available = False
                #         print('⚡️ Shlink is not available (query preview disabled)')

                if not shlink_available:
                    print(f'[{i + 2}/{steps}] Partition #{i + 1}: OK')

        current_map = get_current_map(current_action)

        result: dict[str, list[DiffEntry]] = {
            'node': [],
            'way': [],
            'relation': []
        }

        for element_type, element_old, element_new in changeset_edits:
            if element_new['@changeset'] != changeset_id:
                return '❓ Overpass data is corrupted (bad_changeset)'

            if element_old and int(element_new['@version']) - int(element_old['@version']) != 1:
                return '❓ Overpass data is corrupted (bad_version)'

            if not element_old and int(element_new['@version']) == 2:
                return '❓ Overpass data is corrupted (impossible_create)'

            timestamp = parse_timestamp(element_new['@timestamp'])
            element_id = element_new['@id']
            element_current = current_map[element_type].get(element_id, element_new)

            ensure_visible_tag(element_old)
            ensure_visible_tag(element_new)
            ensure_visible_tag(element_current)

            result[element_type].append(DiffEntry(
                timestamp,
                element_id,
                element_old,
                element_new,
                element_current))

        return result

    def update_parents(self, invert: dict) -> int:
        base_url = self.base_urls[0]

        invert_map = {
            'node': {e['@id']: e for e in invert['node']},
            'way': {e['@id']: e for e in invert['way']},
            'relation': {e['@id']: e for e in invert['relation']}
        }

        deleting_ids = {
            'node': {e['@id'] for e in invert['node'] if e['@visible'] == 'false'},
            'way': {e['@id'] for e in invert['way'] if e['@visible'] == 'false'},
            'relation': {e['@id'] for e in invert['relation'] if e['@visible'] == 'false'}
        }

        if sum(len(el) for el in deleting_ids.values()) == 0:
            return 0

        # TODO: optimize bbox by merging previous bboxes
        query_by_ids = build_query_parents_by_ids(deleting_ids)

        with get_http_client() as c:
            parents_query = f'[timeout:180];{query_by_ids}'
            data = fetch_overpass(c, base_url + '/interpreter', parents_query)
            assert isinstance(data, dict)

        parents = {
            'node': ensure_iterable(data['osm'].get('node', [])),
            'way': ensure_iterable(data['osm'].get('way', [])),
            'relation': ensure_iterable(data['osm'].get('relation', [])),
        }

        fixed_parents = 0

        for element_type, elements in parents.items():
            for element in elements:
                assert isinstance(element, dict)

                # use current element if present
                element_orig = invert_map[element_type].get(element['@id'], element)
                element = deepcopy(element_orig)

                # TODO: ensure default element tags
                if element.get('@visible', 'true') == 'false':
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

                    # TODO: this could be optimized, include id in deleting ids and recurse
                    if not element['member']:
                        element['@visible'] = 'false'
                else:
                    raise

                if element == element_orig:
                    continue

                ensure_visible_tag(element)

                if element['@id'] in invert_map[element_type]:
                    idx = next(i for i, v in enumerate(invert[element_type]) if v['@id'] == element['@id'])
                    invert[element_type][idx] = element
                else:
                    invert[element_type].append(element)

                fixed_parents += 1

        return fixed_parents
