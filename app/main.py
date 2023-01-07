import os

import fire

from config import CREATED_BY
from invert import invert_diff
from osm import OsmApi
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


# TODO: revert specific elements CS(node:123,456;way:123)
def main(changeset_ids: list | str | int, comment: str,
         username: str = None, password: str = None, *,
         oauth_token: str = None, oauth_token_secret: str = None):
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

        print(f'[2/2] Overpass …')
        diff = overpass.get_changeset_elements_history(changeset)
        diffs.append(diff)

    print('🔁 Generating a revert')
    merged_diffs = merge_and_sort_diffs(diffs)
    invert = invert_diff(merged_diffs)

    if all(not elements for elements in invert.values()):
        print(f'✅ Nothing to revert')
        exit(0)

    print('🌍️ Uploading changes')
    if changeset_id := osm.upload_diff(invert, comment, {
        'created_by': CREATED_BY,
        'revert:ids': ';'.join(changeset_ids)
    }):
        print(f'✅ Success ({changeset_id})')
        exit(0)

    exit(-1)


if __name__ == '__main__':
    fire.Fire(main)
