import json
from copy import deepcopy
from typing import Optional

from config import TAG_PREFIX
from utils import ensure_iterable, dmp_retry_reverse


def set_visible_original(target: Optional[dict], current: dict):
    if target and '@visible:original' not in target:
        target['@visible:original'] = current['@visible']


def invert_diff(diff: dict) -> (dict, dict):
    # we need this to make reverting multiple changesets at a time possible
    current_map = {
        'node': {},
        'way': {},
        'relation': {}
    }

    # store latest versions of elements (for osmChange upload)
    version_map = {
        'node': {},
        'way': {},
        'relation': {}
    }

    statistics = {
        'fix:node': 0,
        'fix:way': 0,
        'fix:relation': 0,

        'dmp:way': 0,
        'dmp:way:id': [],
        'dmp:relation': 0,
        'dmp:relation:id': [],

        'dmp:fail:way': 0,
        'dmp:fail:way:id': [],
        'dmp:fail:relation': 0,
        'dmp:fail:relation:id': [],
    }

    for element_type, elements in diff.items():
        for _, element_id, old, new, current in elements:
            if element_id not in version_map[element_type]:
                version_map[element_type][element_id] = current['@version']

            current = current_map[element_type].get(element_id, current)

            set_visible_original(old, current)
            set_visible_original(new, current)
            set_visible_original(current, current)

            # create
            if (not old or old['@visible'] == 'false') and new['@visible'] == 'true':
                # absolute delete
                if current['@visible'] == 'true':
                    current['@visible'] = 'false'
                    current_map[element_type][element_id] = deepcopy(current)

            # modify
            elif old['@visible'] == 'true' and new['@visible'] == 'true':
                # simple revert
                if current['@version'] == new['@version']:
                    current_map[element_type][element_id] = deepcopy(old)

                # advanced revert
                else:
                    print(f'🛠️ Performing advanced revert on {element_type}:{element_id}')
                    statistics[f'fix:{element_type}'] += 1

                    current['tag'] = ensure_iterable(current.get('tag', []))
                    current_original = deepcopy(current)

                    invert_tags(old, new, current)

                    if element_type == 'node':
                        invert_node_position(old, new, current)
                    elif element_type == 'way':
                        invert_way_nodes(old, new, current, statistics)
                    elif element_type == 'relation':
                        invert_relation_members(old, new, current, statistics)
                    else:
                        raise

                    if current != current_original:
                        current_map[element_type][element_id] = deepcopy(current)

            # delete
            elif old['@visible'] == 'true' and new['@visible'] == 'false':
                # do not restore repeatedly deleted elements
                if current['@version'] == new['@version']:
                    current_map[element_type][element_id] = deepcopy(old)

            else:
                raise

    result = {element_type: list(element_id_map.values()) for element_type, element_id_map in current_map.items()}

    for element_type, elements in result.items():
        for element in list(elements):
            element['@version'] = version_map[element_type][element['@id']]

            # don't delete already deleted elements (this may happen during multiple changesets)
            if element['@visible'] == 'false' and element['@visible:original'] == 'false':
                elements.remove(element)
            else:
                del element['@visible:original']

    for key, value in list(statistics.items()):
        if value and isinstance(value, list):
            statistics[key] = ';'.join(value)

    return result, statistics


def invert_tags(old: dict, new: dict, current: dict) -> None:
    old['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(old.get('tag', []))}
    new['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(new.get('tag', []))}
    current['tag'] = {d['@k']: d['@v'] for d in ensure_iterable(current.get('tag', []))}

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


def invert_way_nodes(old: dict, new: dict, current: dict, statistics: dict) -> None:
    old_nodes = [json.dumps(n) for n in ensure_iterable(old.get('nd', []))]
    new_nodes = [json.dumps(n) for n in ensure_iterable(new.get('nd', []))]
    current_nodes = [json.dumps(n) for n in ensure_iterable(current.get('nd', []))]

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

    print(f'💡 Performing DMP patch on way:{new["@id"]}')

    if patch := dmp_retry_reverse(old_nodes, new_nodes, current_nodes):
        current['nd'] = [json.loads(p) for p in patch]
        print(f'[DMP][☑️] Patch successful')
        statistics['dmp:way'] += 1
        statistics['dmp:way:id'].append(new['@id'])
    else:
        statistics['dmp:fail:way'] += 1
        statistics['dmp:fail:way:id'].append(new['@id'])


def invert_relation_members(old: dict, new: dict, current: dict, statistics: dict) -> None:
    old_members = [json.dumps(m) for m in ensure_iterable(old.get('member', []))]
    new_members = [json.dumps(m) for m in ensure_iterable(new.get('member', []))]
    current_members = [json.dumps(m) for m in ensure_iterable(current.get('member', []))]

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

    print(f'💡 Performing DMP patch relation:{new["@id"]}')

    if patch := dmp_retry_reverse(old_members, new_members, current_members):
        current['member'] = [json.loads(p) for p in patch]
        print(f'✅ Patch successful')
        statistics['dmp:relation'] += 1
        statistics['dmp:relation:id'].append(new['@id'])
    else:
        statistics['dmp:fail:relation'] += 1
        statistics['dmp:fail:relation:id'].append(new['@id'])
