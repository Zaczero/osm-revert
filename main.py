import os

from diff_match_patch import diff_match_patch
from osm import OsmApi
from overpass import Overpass
from utils import dmp


def invert_diff(diff: dict) -> dict:
    # TODO: sort by newest
    current_map = {}  # TODO:

    result = {
        'node': [],
        'way': [],
        'relation': []
    }

    for element_type, elements_dict in diff.items():
        for element_id, (old, new) in elements_dict.items():
            current = current_map[element_id]

            # TODO: handle None

            invert_tags_create(old, new, current)
            invert_tags_modify(old, new, current)
            invert_tags_delete(old, new, current)

            if element_type == 'node':
                invert_node_position(old, new, current)
            elif element_type == 'way':
                pass
            elif element_type == 'relation':
                pass
            else:
                raise


def invert_tags_create(old: dict, new: dict, current: dict) -> None:
    old_tags = old['tags']
    new_tags = new['tags']
    current_tags = current['tags']

    changed_items = set(new_tags.items()) - set(old_tags.items())

    for key, value in changed_items:
        # ignore modified
        if key in old_tags:
            continue

        # expect to be new value
        if current_tags.get(key) != value:
            continue

        del current_tags[key]


def invert_tags_modify(old: dict, new: dict, current: dict) -> None:
    old_tags = old['tags']
    new_tags = new['tags']
    current_tags = current['tags']

    changed_items = set(new_tags.items()) - set(old_tags.items())

    for key, value in changed_items:
        # ignore created
        if key not in old_tags:
            continue

        # expect to be new value
        if current_tags.get(key) != value:
            continue

        current_tags[key] = old_tags[key]


def invert_tags_delete(old: dict, new: dict, current: dict) -> None:
    old_tags = old['tags']
    new_tags = new['tags']
    current_tags = current['tags']

    changed_items = set(old_tags.items()) - set(new_tags.items())

    for key, value in changed_items:
        # ignore modified
        if key in new_tags:
            continue

        # expect to be deleted
        if current_tags.get(key) is not None:
            continue

        current_tags[key] = value


def invert_node_position(old: dict, new: dict, current: dict) -> None:
    # ignore unmoved
    if old['@lat'] == new['@lat'] and old['@lon'] == new['@lon']:
        return

    # expect to be at new location
    if current['@lat'] != new['@lat'] or current['@lon'] != new['@lon']:
        return

    current['@lat'] = old['@lat']
    current['@lon'] = old['@lon']


def invert_way_nodes(old: dict, new: dict, current: dict) -> None:
    old_refs = ' '.join(nd['@ref'] for nd in old['nd'])
    new_refs = ' '.join(nd['@ref'] for nd in new['nd'])
    current_refs = ' '.join(nd['@ref'] for nd in current['nd'])

    # ignore unmodified
    if old_refs == new_refs:
        return

    # already reverted
    if old_refs == current_refs:
        return

    # simple revert if no more edits
    if current_refs == new_refs:
        current['nd'] = old['nd']
        return

    dmp = diff_match_patch()
    patch = dmp.patch_make(new_refs, current_refs)
    dmp.patch_apply(patch, old_refs)


def main():
    old_refs = '1\n2\n3\n'
    new_refs = '1\n8\n2\n5\n'
    current_refs = '1\n8\n3\n6\n'

    x = dmp('123', '138', '13')

    # TODO: check duplicates, check existing

    changeset_id = '130869033'
    changeset_id = int(changeset_id)

    osm = OsmApi(os.getenv('USER'), os.getenv('PASS'))
    overpass = Overpass()

    changeset = osm.get_changeset(changeset_id)
    diff = overpass.get_changeset_elements_history(changeset)

    x = 1


if __name__ == '__main__':
    main()
