import os

from osm import OsmApi
from overpass import Overpass


def invert_diff(diff: dict) -> dict:
    result = {
        'node': [],
        'way': [],
        'relation': []
    }

    for element_type, elements in diff.items():
        for old, new, current in elements:




def main():
    changeset_id = '130869033'
    changeset_id = int(changeset_id)

    osm = OsmApi(os.getenv('USER'), os.getenv('PASS'))
    overpass = Overpass()

    changeset = osm.full_changeset_download(changeset_id)
    diff = overpass.get_changeset_diff(changeset)

    x = 1


if __name__ == '__main__':
    main()
