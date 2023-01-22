import os
import time
import traceback
from typing import Iterable

import fire
import xmltodict

from config import CREATED_BY, WEBSITE, CHANGESETS_LIMIT_CONFIG
from invert import invert_diff
from osm import OsmApi, build_osm_change
from overpass import Overpass
from utils import ensure_iterable


def build_element_ids_dict(element_ids: Iterable[str]) -> dict[str, dict[str, set[str]]]:
    result = {
        'include': {
            'node': set(),
            'way': set(),
            'relation': set()
        },
        'exclude': {
            'node': set(),
            'way': set(),
            'relation': set()
        }
    }

    prefixes = {
        'node': ('nodes', 'node', 'n'),
        'way': ('ways', 'way', 'w'),
        'relation': ('relations', 'relation', 'rel', 'r')
    }

    for element_id in element_ids:
        element_id = element_id.strip().lower()

        if element_id.startswith('-'):
            element_result = result['exclude']
            element_id = element_id.lstrip('-').lstrip()
        else:
            element_result = result['include']
            element_id = element_id.lstrip('+').lstrip()

        for element_type, value in prefixes.items():
            for prefix in value:
                if element_id.startswith(prefix):
                    element_id = element_id[len(prefix):].lstrip(':;.,').lstrip()

                    if not element_id.isnumeric():
                        raise Exception(f'{element_type.title()} element id must be numeric: {element_id}')

                    element_result[element_type].add(element_id)
                    break
            else:
                continue
            break

        else:
            raise Exception(f'Unknown element filter format: {element_id}')

    return result


def merge_and_sort_diffs(diffs: list[dict]) -> dict:
    result = diffs[0]

    for diff in diffs[1:]:
        for element_type, elements in diff.items():
            result[element_type] += elements

    for element_type, elements in result.items():
        # sort by newest edits first
        result[element_type] = sorted(elements, key=lambda t: t[0], reverse=True)

    return result


def main_timer(func):
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()

        try:
            exit_code = func(*args, **kwargs)
        except Exception:
            traceback.print_exc()
            exit_code = -2

        total_time = time.perf_counter() - start_time
        print(f'🏁 Total time: {total_time:.1F} sec')
        exit(exit_code)

    return wrapper


# TODO: util function to ensure tags existence and type
# TODO: slow but very accurate revert (download full history of future edits); overpass-api: timeline
# TODO: dataclasses
@main_timer
def main(changeset_ids: list | str | int, comment: str,
         username: str = None, password: str = None, *,
         oauth_token: str = None, oauth_token_secret: str = None,
         osc_file: str = None, print_osc: bool = None,
         query_filter: str = '') -> int:
    changeset_ids = list(sorted(set(
        str(changeset_id).strip() for changeset_id in ensure_iterable(changeset_ids) if changeset_id
    )))
    assert changeset_ids, 'Missing changeset id'
    assert all(c.isnumeric() for c in changeset_ids), 'Changeset ids must be numeric'

    if not username and not password:
        username = os.getenv('OSM_USERNAME')
        password = os.getenv('OSM_PASSWORD')

    print('🔒️ Logging in to OpenStreetMap')
    osm = OsmApi(username=username, password=password, oauth_token=oauth_token, oauth_token_secret=oauth_token_secret)
    user = osm.get_authorized_user()

    user_edits = user['changesets']['count']
    user_is_moderator = 'moderator' in user['roles'] or 'administrator' in user['roles']

    print(f'👤 Welcome, {user["display_name"]}{" 🔷" if user_is_moderator else ""}!')

    changesets_limit_config = CHANGESETS_LIMIT_CONFIG['moderator' if user_is_moderator else '']
    changesets_limit = max(v for k, v in changesets_limit_config.items() if k <= user_edits)

    if changesets_limit == 0:
        min_edits = min(k for k in changesets_limit_config.keys() if k > 0)
        print(f'🐥 You need to make at least {min_edits} edits to use this tool')
        return -1

    if changesets_limit < len(changeset_ids):
        print(f'🛟 For safety, you can only revert up to {changesets_limit} changesets at a time')

        if limit_increase := min((k for k in changesets_limit_config.keys() if k > user_edits), default=None):
            print(f'🛟 To increase this limit, make at least {limit_increase} edits')

        return -1

    overpass = Overpass()
    diffs = []

    for changeset_id in changeset_ids:
        changeset_id = int(changeset_id)
        print(f'☁️ Downloading changeset {changeset_id}')

        print(f'[1/?] OpenStreetMap …')
        changeset = osm.get_changeset(changeset_id)
        changeset_size = sum(len(v) for p in changeset['partition'].values() for v in p.values())
        partition_count = len(changeset['partition'])
        steps = partition_count + 1

        print(f'[1/{steps}] OpenStreetMap: {changeset_size} element{"s" if changeset_size > 1 else ""}')

        if changeset_size:
            if partition_count > 2:
                print(f'[2/{steps}] Overpass ({partition_count} partitions, this may take a while) …')
            else:
                print(f'[2/{steps}] Overpass ({partition_count} partition{"s" if partition_count > 1 else ""}) …')

            diff = overpass.get_changeset_elements_history(changeset, steps, query_filter)

            if not diff:
                return -1

            diffs.append(diff)
            diff_size = sum(len(el) for el in diff.values())

            assert diff_size <= changeset_size, \
                f'Diff must not be larger than changeset size: {diff_size=}, {changeset_size=}'

            if query_filter:
                print(f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""} (🪣 filtered)')
            else:
                print(f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""}')

    print('🔁 Generating a revert')
    merged_diffs = merge_and_sort_diffs(diffs)

    invert, statistics = invert_diff(merged_diffs)
    parents = overpass.update_parents(invert)

    if parents:
        print(f'🛠️ Fixing {parents} parent{"s" if parents > 1 else ""}')

    invert_size = sum(len(elements) for elements in invert.values())

    if invert_size == 0:
        print('✅ Nothing to revert')
        return 0

    changeset_max_size = osm.get_changeset_max_size()

    if invert_size > changeset_max_size:
        print(f'🐘 Revert is too big: {invert_size} > {changeset_max_size}')

        if len(changeset_ids) > 1:
            print(f'🐘 Hint: Try reducing the amount of changesets to revert at once')

        return -1

    if osc_file or print_osc:
        print(f'💾 Saving {invert_size} change{"s" if invert_size > 1 else ""} to .osc')

        osm_change = build_osm_change(invert, changeset_id=None)
        osm_change_xml = xmltodict.unparse(osm_change, pretty=True)

        if osc_file:
            with open(osc_file, 'w', encoding='utf-8') as f:
                f.write(osm_change_xml)

        if print_osc:
            print('<osc>')
            print(osm_change_xml)
            print('</osc>')

        print(f'✅ Success')
        return 0

    else:
        print(f'🌍️ Uploading {invert_size} change{"s" if invert_size > 1 else ""}')

        if len(changeset_ids) == 1:
            changeset_ids = [f'https://www.openstreetmap.org/changeset/{c}' for c in changeset_ids]

        extra_args = {
            'created_by': CREATED_BY,
            'website': WEBSITE,
            'id': ';'.join(changeset_ids)
        }

        if query_filter:
            extra_args['filter'] = query_filter

        if changeset_id := osm.upload_diff(invert, comment, extra_args | statistics):
            print(f'✅ Success')
            print(f'✅ https://www.openstreetmap.org/changeset/{changeset_id}')
            return 0

    return -1


if __name__ == '__main__':
    fire.Fire(main)
