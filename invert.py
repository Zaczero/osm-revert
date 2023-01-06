import json
from copy import deepcopy

from utils import ensure_iterable, dmp_retry_reverse


def invert_diff(diff: dict) -> dict:
    result = {
        'node': [],
        'way': [],
        'relation': []
    }

    version_map = {
        'node': {},
        'way': {},
        'relation': {}
    }

    # we need this to make reverting multiple changesets at a time possible
    current_map = {
        'node': {},
        'way': {},
        'relation': {}
    }

    # TODO: visible false -> visible true

    for element_type, elements in diff.items():
        for _, element_id, old, new, current in elements:
            if element_id not in version_map[element_type]:
                version_map[element_type][element_id] = current['@version']

            current = current_map[element_type].get(element_id, current)

            # create
            if not old and new['@visible'] == 'true':
                # absolute delete
                if current['@visible'] == 'true':
                    current['@visible'] = 'false'

                    result[element_type].append(current)
                    current_map[element_type][element_id] = deepcopy(current)

            # modify
            elif old['@visible'] == 'true' and new['@visible'] == 'true':
                # simple revert
                if current['@version'] == new['@version']:
                    current = old

                    result[element_type].append(current)
                    current_map[element_type][element_id] = deepcopy(current)

                # advanced revert
                else:
                    current_original = deepcopy(current)

                    invert_tags(old, new, current)

                    if element_type == 'node':
                        invert_node_position(old, new, current)
                    elif element_type == 'way':
                        invert_way_nodes(old, new, current)
                    elif element_type == 'relation':
                        invert_relation_members(old, new, current)
                    else:
                        raise

                    if current != current_original:
                        result[element_type].append(current)
                        current_map[element_type][element_id] = deepcopy(current)

            # delete
            elif old['@visible'] == 'true' and new['@visible'] == 'false':
                # do not restore repeatedly deleted elements
                if current['@version'] == new['@version']:
                    current = old

                    result[element_type].append(current)
                    current_map[element_type][element_id] = deepcopy(current)

            else:
                raise

    for element_type, elements in result.items():
        for element in elements:
            element['@version'] = version_map[element_type][element['@id']]

    return result


def invert_tags(old: dict, new: dict, current: dict) -> None:
    old['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(old['tag'])}
    new['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(new['tag'])}
    current['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(current['tag'])}

    invert_tags_create(old, new, current)
    invert_tags_modify(old, new, current)
    invert_tags_delete(old, new, current)

    old['tag'] = [{'@k': k, '@v': v} for k, v in old['tag'].items()]
    new['tag'] = [{'@k': k, '@v': v} for k, v in new['tag'].items()]
    current['tag'] = [{'@k': k, '@v': v} for k, v in current['tag'].items()]


def invert_tags_create(old: dict, new: dict, current: dict) -> None:
    old_tags = old['tag']
    new_tags = new['tag']
    current_tags = current['tag']

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
    old_tags = old['tag']
    new_tags = new['tag']
    current_tags = current['tag']

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
    old_tags = old['tag']
    new_tags = new['tag']
    current_tags = current['tag']

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
    old_nodes = [json.dumps(n) for n in ensure_iterable(old['nd'])]
    new_nodes = [json.dumps(n) for n in ensure_iterable(new['nd'])]
    current_nodes = [json.dumps(n) for n in ensure_iterable(current['nd'])]

    # ignore unmodified
    if old_nodes == new_nodes:
        return

    # already reverted
    if current_nodes != new_nodes and set(old_nodes) == set(current_nodes):
        return

    # simple revert if no more edits
    if current_nodes == new_nodes:
        current['nd'] = old['nd']
        return

    if patch := dmp_retry_reverse(old_nodes, new_nodes, current_nodes):
        current['nd'] = [json.loads(p) for p in patch]


def invert_relation_members(old: dict, new: dict, current: dict) -> None:
    old_members = [json.dumps(m) for m in ensure_iterable(old['member'])]
    new_members = [json.dumps(m) for m in ensure_iterable(new['member'])]
    current_members = [json.dumps(m) for m in ensure_iterable(current['member'])]

    # ignore unmodified
    if old_members == new_members:
        return

    # already reverted
    if current_members != new_members and set(old_members) == set(current_members):
        return

    # simple revert if no more edits
    if current_members == new_members:
        current['member'] = old['member']
        return

    if patch := dmp_retry_reverse(old_members, new_members, current_members):
        current['member'] = [json.loads(p) for p in patch]
