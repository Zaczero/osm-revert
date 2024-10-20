import html
import re
from collections.abc import Iterable, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from itertools import chain, pairwise

import xmltodict
from httpx import AsyncClient

from osm_revert.config import OVERPASS_URLS, REVERT_TO_DATE
from osm_revert.context_logger import context_print
from osm_revert.diff_entry import DiffEntry
from osm_revert.utils import ensure_iterable, get_http_client, retry_exponential


def parse_timestamp(timestamp: str) -> int:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    return int(datetime.strptime(timestamp, date_format).replace(tzinfo=UTC).timestamp())


def get_bbox(changeset: dict) -> str:
    e = changeset['osm']['changeset']

    # some changesets don't have a bbox
    if '@min_lat' not in e:
        return ''

    min_lat, max_lat = e['@min_lat'], e['@max_lat']
    min_lon, max_lon = e['@min_lon'], e['@max_lon']
    return f'[bbox:{min_lat},{min_lon},{max_lat},{max_lon}]'


def get_old_date(timestamp: str) -> str:
    date_format = '%Y-%m-%dT%H:%M:%SZ'
    date = datetime.strptime(timestamp, date_format).replace(tzinfo=UTC)
    created_at_minus_one = (date - timedelta(seconds=1)).strftime(date_format)

    return f'[date:"{created_at_minus_one}"]'


def get_new_date(timestamp: str) -> str:
    return f'[date:"{timestamp}"]'


def get_changeset_adiff(timestamp: str) -> str:
    if REVERT_TO_DATE is None:
        date_format = '%Y-%m-%dT%H:%M:%SZ'
        date = datetime.strptime(timestamp, date_format).replace(tzinfo=UTC)
        created_at_minus_one = (date - timedelta(seconds=1)).strftime(date_format)
        return f'[adiff:"{created_at_minus_one}","{timestamp}"]'
    else:
        return f'[adiff:"{REVERT_TO_DATE}","{timestamp}"]'


def get_current_adiff(timestamp: str) -> str:
    return f'[adiff:"{timestamp}"]'


@lru_cache(maxsize=128)
def get_element_types_from_selector(selector: str) -> Sequence[str]:
    if selector in {'node', 'way', 'relation'}:
        return (selector,)
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
    for match in sorted(
        re.finditer(r'\brel\b', query_filter),
        key=lambda m: m.start(),
        reverse=True,
    ):
        start, end = match.start(), match.end()
        query_filter = query_filter[:start] + 'relation' + query_filter[end:]

    # handle custom (!id:)
    for match in sorted(
        re.finditer(r'\(\s*!\s*id\s*:(?P<id>(\s*(,\s*)?\d+)+)\s*\)', query_filter),
        key=lambda m: m.start(),
        reverse=True,
    ):
        start, end = match.start(), match.end()
        invert_ids = (i.strip() for i in match.group('id').split(',') if i.strip())
        selector = re.match(r'.*\b(nwr|nw|nr|wr|node|way|relation)\b', query_filter[:start], re.DOTALL).group(1)

        new_ids = set(chain.from_iterable(element_ids[et] for et in get_element_types_from_selector(selector)))
        new_ids = new_ids.difference(invert_ids)
        joined_new_ids = ','.join(new_ids)

        query_filter = query_filter[:start] + f'(id:{joined_new_ids})' + query_filter[end:]

    # apply element id filtering
    for match in sorted(
        re.finditer(r'\b(nwr|nw|nr|wr|node|way|relation)\b', query_filter),
        key=lambda m: m.start(),
        reverse=True,
    ):
        end = match.end()
        selector = match.group(1)

        joined_element_ids = ','.join(
            set(chain.from_iterable(element_ids[et] for et in get_element_types_from_selector(selector)))
        )

        query_filter = query_filter[:end] + f'(id:{joined_element_ids})' + query_filter[end:]

    if implicit_query_way_children:
        return f'({query_filter});out meta;node(w);out meta;'
    else:
        return f'({query_filter});out meta;'


def build_query_parents_by_ids(element_ids: dict) -> str:
    return (
        f'node(id:{",".join(element_ids["node"]) if element_ids["node"] else "-1"})->.n;'
        f'way(id:{",".join(element_ids["way"]) if element_ids["way"] else "-1"})->.w;'
        f'rel(id:{",".join(element_ids["relation"]) if element_ids["relation"] else "-1"})->.r;'
        f'(way(bn.n);rel(bn.n);rel(bw.w);rel(br.r););'
        f'out meta;'
    )


@retry_exponential
async def fetch_overpass(http: AsyncClient, data: str, *, check_bad_request: bool = False) -> dict | str:
    r = await http.post('/interpreter', data={'data': data}, timeout=300)

    if check_bad_request and r.status_code == 400:
        s = r.text.find('<body>')
        e = r.text.find('</body>')
        if e > s > -1:
            body = r.text[s + 6 : e].strip()
            body = re.sub(r'<.*?>', '', body, flags=re.DOTALL)
            lines = tuple(
                html.unescape(line.strip()[7:])  #
                for line in body.split('\n')
                if line.strip().startswith('Error: ')
            )
            if lines:
                return '🛑 Overpass - Bad Request:\n' + '\n'.join(f'🛑 {line}' for line in lines)

    r.raise_for_status()  # TODO: return error message instead raise
    return xmltodict.parse(r.text)


def get_current_map(actions: Iterable[dict]) -> dict[str, dict[str, dict]]:
    result = {'node': {}, 'way': {}, 'relation': {}}
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
        raise NotImplementedError(f'Unknown action type: {action["@type"]}')
    return element_type, element_old, element_new


def ensure_visible_tag(element: dict | None) -> None:
    if not element:
        return
    if '@visible' not in element:
        element['@visible'] = 'true'


class Overpass:
    def __init__(self):
        self._https = tuple(get_http_client(url) for url in OVERPASS_URLS)

    async def get_changeset_elements_history(
        self,
        changeset: dict,
        steps: int,
        query_filter: str,
    ) -> dict[str, list[DiffEntry]] | None:
        errors = []

        for http in self._https:
            if errors:
                context_print(f'[2/{steps}] Retrying …')

            result = await self._get_changeset_elements_history(http, changeset, steps, query_filter)

            if isinstance(result, dict):  # everything ok
                return result

            errors.append(result)

        # all errors are the same
        if all(errors[0] == e for e in errors[1:]):
            context_print(f'{errors[0]} (x{len(errors)})')
        else:
            context_print('❗️ Multiple errors occurred:')
            for i, error in enumerate(errors):
                context_print(f'[{i + 1}/{len(errors)}]: {error}')

        return None

    async def _get_changeset_elements_history(
        self,
        http: AsyncClient,
        changeset: dict,
        steps: int,
        query_filter: str,
    ) -> dict[str, list[DiffEntry]] | str:
        bbox = get_bbox(changeset)
        changeset_id = changeset['osm']['changeset']['@id']
        changeset_edits = []
        current_action = []

        for i, (timestamp, element_ids) in enumerate(sorted(changeset['partition'].items(), key=lambda t: t[0])):
            partition_adiff = get_changeset_adiff(timestamp)
            current_adiff = get_current_adiff(timestamp)
            query_unfiltered = build_query_filtered(element_ids, '')

            partition_query = f'[timeout:180]{bbox}{partition_adiff};{query_unfiltered}'
            partition_diff = await fetch_overpass(http, partition_query)

            if isinstance(partition_diff, str):
                return partition_diff

            partition_action = ensure_iterable(partition_diff['osm'].get('action', ()))

            if parse_timestamp(partition_diff['osm']['meta']['@osm_base']) <= parse_timestamp(timestamp):
                return '🕒️ Overpass is updating, please try again shortly'

            partition_size = len(partition_action)
            query_size = sum(len(v) for v in element_ids.values())

            if partition_size != query_size:
                return f'❓️ Overpass data is incomplete: {partition_size} != {query_size}'

            if query_filter:
                query_filtered = build_query_filtered(element_ids, query_filter)

                filtered_query = f'[timeout:180]{bbox}{partition_adiff};{query_filtered}'
                filtered_diff = await fetch_overpass(http, filtered_query, check_bad_request=True)

                if isinstance(filtered_diff, str):
                    return filtered_diff

                filtered_action = ensure_iterable(filtered_diff['osm'].get('action', ()))

                dedup_node_ids = set()
                data_map = {'node': {}, 'way': {}, 'relation': {}}

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
                        return '❓️ Overpass data is incomplete (missing_merge)'

                    if old_new_t[1]['@version'] != element_new['@version']:
                        return '❓️ Overpass data is incomplete (bad_merge_version)'

                    changeset_edits.append((element_type, *old_new_t))

            else:
                changeset_edits.extend(parse_action(a) for a in partition_action)

            current_query = f'[timeout:180]{bbox}{current_adiff};{query_unfiltered}'
            current_diff = await fetch_overpass(http, current_query)

            if isinstance(current_diff, str):
                return current_diff

            current_partition_action = ensure_iterable(current_diff['osm'].get('action', ()))
            current_action.extend(current_partition_action)

            context_print(f'[{i + 2}/{steps}] Partition #{i + 1}: OK')

        current_map = get_current_map(current_action)

        result: dict[str, list[DiffEntry]] = {'node': [], 'way': [], 'relation': []}

        for element_type, element_old, element_new in changeset_edits:
            # TODO: skip checks by time
            # NOTE: this may happen legitimately when there are multiple changesets at the same time
            # if element_new['@changeset'] != changeset_id:
            #     return '❓ Overpass data is corrupted (bad_changeset)'

            # NOTE: this may happen legitimately when there are multiple changesets at the same time
            # if element_old and int(element_new['@version']) - int(element_old['@version']) != 1:
            #     return '❓ Overpass data is corrupted (bad_version)'

            # NOTE: this may happen legitimately when there are multiple changesets at the same time
            # if not element_old and int(element_new['@version']) == 2 and not REVERT_TO_DATE:
            #     return '❓ Overpass data is corrupted (impossible_create)'

            timestamp = parse_timestamp(element_new['@timestamp'])
            element_id = element_new['@id']
            element_current = current_map[element_type].get(element_id, element_new)

            ensure_visible_tag(element_old)
            ensure_visible_tag(element_new)
            ensure_visible_tag(element_current)

            result[element_type].append(DiffEntry(timestamp, element_id, element_old, element_new, element_current))

        return result

    async def update_parents(self, invert: dict[str, list], fix_parents: bool) -> int:
        internal_ids = {
            'node': {e['@id'] for e in invert['node']},
            'way': {e['@id'] for e in invert['way']},
            'relation': {e['@id'] for e in invert['relation']},
        }
        counter = 0

        for _ in range(10):
            deleting_ids = {
                'node': {e['@id'] for e in invert['node'] if e['@visible'] == 'false'},
                'way': {e['@id'] for e in invert['way'] if e['@visible'] == 'false'},
                'relation': {e['@id'] for e in invert['relation'] if e['@visible'] == 'false'},
            }

            if not any(ids for ids in deleting_ids.values()):
                return counter

            # TODO: optimize bbox by merging previous bboxes
            # TODO: optimize processing by not processing the same deleted ids multiple times
            query_by_ids = build_query_parents_by_ids(deleting_ids)

            parents_query = f'[timeout:180];{query_by_ids}'
            data = await fetch_overpass(self._https[0], parents_query)

            if isinstance(data, str):
                return data

            invert_map = {
                'node': {e['@id']: e for e in invert['node']},
                'way': {e['@id']: e for e in invert['way']},
                'relation': {e['@id']: e for e in invert['relation']},
            }

            parents = {
                'node': ensure_iterable(data['osm'].get('node', ())),
                'way': ensure_iterable(data['osm'].get('way', ())),
                'relation': ensure_iterable(data['osm'].get('relation', ())),
            }

            changed = False

            for element_type, elements in parents.items():
                for element in elements:
                    element: dict

                    # skip internal elements when not fixing parents
                    if not fix_parents and element['@id'] in internal_ids[element_type]:
                        continue

                    # use current element if present
                    element = deepcopy(invert_map[element_type].get(element['@id'], element))

                    # TODO: ensure default element tags
                    # skip if parent is already deleted
                    if element.get('@visible', 'true') == 'false':
                        continue

                    deleting_child_ids = {'node': set(), 'way': set(), 'relation': set()}

                    if element_type == 'way':
                        element['nd'] = ensure_iterable(element.get('nd', ()))
                        new_nds = []

                        for nd in element['nd']:
                            if nd['@ref'] in deleting_ids['node']:
                                deleting_child_ids['node'].add(nd['@ref'])
                            else:
                                new_nds.append(nd)

                        element['nd'] = new_nds

                        # delete single node ways
                        if len(element['nd']) == 1:
                            element['nd'] = ()

                        if not element['nd']:
                            element['@visible'] = 'false'

                    elif element_type == 'relation':
                        element['member'] = ensure_iterable(element.get('member', ()))
                        new_members = []

                        for m in element['member']:
                            if m['@ref'] in deleting_ids[m['@type']]:
                                deleting_child_ids[m['@type']].add(m['@ref'])
                            else:
                                new_members.append(m)

                        element['member'] = new_members

                        if not element['member']:
                            element['@visible'] = 'false'

                    else:
                        raise NotImplementedError(f'Unknown element type: {element_type}')

                    # skip if nothing changed
                    if not any(ids for ids in deleting_child_ids.values()):
                        continue

                    changed = True

                    if fix_parents:
                        ensure_visible_tag(element)

                        if element['@id'] in invert_map[element_type]:
                            idx = next(i for i, v in enumerate(invert[element_type]) if v['@id'] == element['@id'])
                            invert[element_type][idx] = element
                        else:
                            invert[element_type].append(element)
                            counter += 1

                    else:
                        for key, ids in deleting_child_ids.items():
                            invert_key_idxs = []

                            for id_ in ids:
                                idx = next((i for i, v in enumerate(invert[key]) if v['@id'] == id_), None)
                                if idx is not None:
                                    invert_key_idxs.append(idx)
                                    internal_ids[key].remove(id_)
                                    counter += 1

                            if not invert_key_idxs:
                                continue

                            invert_key_idxs.sort()

                            invert[key] = list(
                                chain(
                                    invert[key][: invert_key_idxs[0]],
                                    *(invert[key][left + 1 : right] for left, right in pairwise(invert_key_idxs)),
                                    invert[key][invert_key_idxs[-1] + 1 :],
                                )
                            )

            if not changed:
                return counter

        raise RecursionError('Parents recursion limit reached')
