import os

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


def main():
    changeset_id = '130847690'
    changeset_id = int(changeset_id)

    osm = OsmApi(os.getenv('USER'), os.getenv('PASS'))
    overpass = Overpass()

    changeset = osm.get_changeset(changeset_id)
    diff_1 = overpass.get_changeset_elements_history(changeset)

    diff = merge_and_sort_diffs([diff_1])
    invert = invert_diff(diff)

    x = 1


if __name__ == '__main__':
    main()
