import os

import fire

from invert import invert_diff
from osm import OsmApi
from overpass import Overpass


def merge_and_sort_diffs(diffs: list[dict]) -> dict:
    result = diffs[0]

    for diff in diffs[1:]:
        for element_type, elements in diff.items():
            result[element_type] += elements

    for element_type, elements in result.items():
        # sort by newest edits first
        result[element_type] = sorted(elements, key=lambda t: t[0], reverse=True)

    return result


def main(changeset_ids: list[str]):
    osm = OsmApi(os.getenv('USER'), os.getenv('PASS'))
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
    invert = invert_diff(merge_and_sort_diffs(diffs))

    x = 1


if __name__ == '__main__':
    fire.Fire(main)
