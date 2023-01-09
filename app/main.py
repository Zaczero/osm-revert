import os
import time
import traceback

import fire
import xmltodict

from config import CREATED_BY, WEBSITE
from invert import invert_diff
from osm import OsmApi, build_osm_change
from overpass import Overpass
from utils import ensure_iterable


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


# TODO: revert specific elements CS(node:123,456;way:123) + subset of their elements(?)
# TODO: util function to ensure tags existence and type
@main_timer
def main(changeset_ids: list | str | int, comment: str,
         username: str = None, password: str = None, *,
         oauth_token: str = None, oauth_token_secret: str = None,
         osc_file: str = None, print_osc: bool = None) -> int:
    changeset_ids = list(sorted(set(str(changeset_id).strip() for changeset_id in ensure_iterable(changeset_ids))))
    assert changeset_ids, 'Missing changeset id'

    if not username and not password:
        username = os.getenv('OSM_USERNAME')
        password = os.getenv('OSM_PASSWORD')

    print('🔒️ Logging in to OpenStreetMap')
    osm = OsmApi(username=username, password=password, oauth_token=oauth_token, oauth_token_secret=oauth_token_secret)
    print(f'👤 Welcome, {osm.get_authorized_display_name()}!')

    overpass = Overpass()

    diffs = []

    for changeset_id in changeset_ids:
        changeset_id = int(changeset_id)
        print(f'☁️ Downloading changeset {changeset_id}')

        print(f'[1/2] OpenStreetMap …')
        changeset = osm.get_changeset(changeset_id)
        changeset_partition_size = len(changeset['partition'])

        if changeset_partition_size > 1:
            print(f'[2/2] Overpass ({changeset_partition_size} partitions) …')
        else:
            print(f'[2/2] Overpass …')

        diff = overpass.get_changeset_elements_history(changeset)

        if not diff:
            return -1

        diffs.append(diff)

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

        if changeset_id := osm.upload_diff(invert, comment, {
                                                                'created_by': CREATED_BY,
                                                                'website': WEBSITE,
                                                                'id': ';'.join(changeset_ids)
                                                            } | statistics):
            print(f'✅ Success')
            print(f'✅ https://www.openstreetmap.org/changeset/{changeset_id}')
            return 0

    return -1


if __name__ == '__main__':
    fire.Fire(main)
