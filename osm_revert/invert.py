import json
from copy import deepcopy

from osm_revert.diff_entry import DiffEntry
from osm_revert.dmp_utils import dmp_retry_reverse
from osm_revert.utils import ensure_iterable, limit_execution_count


def _set_visible_original(target: dict | None, current: dict):
    if target and '@visible:original' not in target:
        target['@visible:original'] = current['@visible']


class Inverter:
    def __init__(self, only_tags: frozenset[str]) -> None:
        self._only_tags = only_tags

        # we need this to make reverting multiple changesets at a time possible
        self._current_map = {'node': {}, 'way': {}, 'relation': {}}

        # store latest versions of elements (for osmChange upload)
        self._version_map = {'node': {}, 'way': {}, 'relation': {}}

        self.statistics: dict = {
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

        self.warnings: dict[str, list[str]] = {'node': [], 'way': [], 'relation': []}

    def invert_diff(self, diff: dict[str, list[DiffEntry]]) -> dict:
        for element_type, elements in diff.items():
            for entry in elements:
                element_id = entry.element_id
                old = entry.element_old
                new = entry.element_new
                current = entry.element_current

                if element_id not in self._version_map[element_type]:
                    self._version_map[element_type][element_id] = current['@version']

                last_current = self._current_map[element_type].get(element_id, None)
                if last_current is not None:
                    current = deepcopy(last_current)

                _set_visible_original(old, current)
                _set_visible_original(new, current)
                _set_visible_original(current, current)

                self._invert_element(element_type, element_id, old, new, current)

        result = {
            element_type: list(element_id_map.values()) for element_type, element_id_map in self._current_map.items()
        }

        for element_type, elements in result.items():
            for element in list(elements):
                # restore latest version number (for valid osmChange)
                element['@version'] = self._version_map[element_type][element['@id']]

                # don't delete already deleted elements (this may happen during multiple changesets)
                if element['@visible'] == 'false' and element['@visible:original'] == 'false':
                    elements.remove(element)
                else:
                    del element['@visible:original']

        # convert [a, b, c] to 'a;b;c'
        for key, value in self.statistics.items():
            if value and isinstance(value, list):
                self.statistics[key] = ';'.join(value)

        return result

    def _invert_element(self, element_type: str, element_id: str, old: dict, new: dict, current: dict) -> None:
        # create
        if (not old or old['@visible'] == 'false') and new['@visible'] == 'true':
            # ignore only_tags mode
            if self._only_tags:
                return

            # absolute delete
            if current['@visible'] == 'true':
                current['@visible'] = 'false'
                self._current_map[element_type][element_id] = current

        # modify
        elif old['@visible'] == 'true' and new['@visible'] == 'true':
            # simple revert; only_tags mode requires advanced revert
            if current['@version'] == new['@version'] and not self._only_tags:
                self._current_map[element_type][element_id] = old

            # advanced revert (element currently is not deleted)
            elif current['@visible'] == 'true':
                if not limit_execution_count('advanced revert', 50):
                    print(f'🛠️ Performing advanced revert on {element_type}/{element_id}')

                self.statistics[f'fix:{element_type}'] += 1

                current['tag'] = ensure_iterable(current.get('tag', []))
                current_original = deepcopy(current)

                self._invert_tags(old, new, current)

                if not self._only_tags:
                    if element_type == 'node':
                        self._invert_node_position(old, new, current)
                    elif element_type == 'way':
                        self._invert_way_nodes(old, new, current)
                    elif element_type == 'relation':
                        self._invert_relation_members(old, new, current)
                    else:
                        raise NotImplementedError(f'Unknown element type: {element_type}')

                if current != current_original:
                    self._current_map[element_type][element_id] = current

        # delete
        elif old['@visible'] == 'true' and new['@visible'] == 'false':
            # ignore only_tags mode
            if self._only_tags:
                return

            # do not restore repeatedly deleted elements
            if current['@version'] == new['@version']:
                self._current_map[element_type][element_id] = old

        else:
            raise Exception(f'Invalid state: {old!r}, {new!r}')

    def _invert_tags(self, old: dict, new: dict, current: dict) -> None:
        old_tags = {d['@k']: d['@v'] for d in ensure_iterable(old.get('tag', []))}
        new_tags = {d['@k']: d['@v'] for d in ensure_iterable(new.get('tag', []))}
        current_tags = {d['@k']: d['@v'] for d in ensure_iterable(current.get('tag', []))}

        self._invert_tags_create(old_tags, new_tags, current_tags)
        self._invert_tags_modify(old_tags, new_tags, current_tags)
        self._invert_tags_delete(old_tags, new_tags, current_tags)

        current['tag'] = [{'@k': k, '@v': v} for k, v in current_tags.items()]

    def _invert_tags_create(self, old_tags: dict, new_tags: dict, current_tags: dict) -> None:
        changed_items = set(new_tags.items()) - set(old_tags.items())

        for key, value in changed_items:
            # ignore only_tags mode
            if self._only_tags and key not in self._only_tags:
                continue

            # ignore modified
            if key in old_tags:
                continue

            # expect to be new value
            if current_tags.get(key) != value:
                continue

            del current_tags[key]

    def _invert_tags_modify(self, old_tags: dict, new_tags: dict, current_tags: dict) -> None:
        changed_items = set(new_tags.items()) - set(old_tags.items())

        for key, value in changed_items:
            # ignore only_tags mode
            if self._only_tags and key not in self._only_tags:
                continue

            # ignore created
            if key not in old_tags:
                continue

            # expect to be new value
            if current_tags.get(key) != value:
                continue

            current_tags[key] = old_tags[key]

    def _invert_tags_delete(self, old_tags: dict, new_tags: dict, current_tags: dict) -> None:
        changed_items = set(old_tags.items()) - set(new_tags.items())

        for key, value in changed_items:
            # ignore only_tags mode
            if self._only_tags and key not in self._only_tags:
                continue

            # ignore modified
            if key in new_tags:
                continue

            # expect to be deleted
            if current_tags.get(key) is not None:
                continue

            current_tags[key] = value

    def _invert_node_position(self, old: dict, new: dict, current: dict) -> None:
        # ignore unmoved
        if old['@lat'] == new['@lat'] and old['@lon'] == new['@lon']:
            return

        # expect to be at new location
        if current['@lat'] != new['@lat'] or current['@lon'] != new['@lon']:
            return

        current['@lat'] = old['@lat']
        current['@lon'] = old['@lon']

    def _invert_way_nodes(self, old: dict, new: dict, current: dict) -> None:
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

        print(f'💡 Performing DMP patch on way/{new["@id"]}')

        if patch := dmp_retry_reverse(old_nodes, new_nodes, current_nodes):
            current['nd'] = [json.loads(p) for p in patch]
            print('[DMP][☑️] Patch successful')
            self.statistics['dmp:way'] += 1
            self.statistics['dmp:way:id'].append(new['@id'])
        else:
            # absolute delete
            create_diff = {n['@ref'] for n in ensure_iterable(new.get('nd', []))}
            create_diff = create_diff.difference(n['@ref'] for n in ensure_iterable(old.get('nd', [])))
            current['nd'] = [n for n in ensure_iterable(current.get('nd', [])) if n['@ref'] not in create_diff]

            self.statistics['dmp:fail:way'] += 1
            self.statistics['dmp:fail:way:id'].append(new['@id'])
            self.warnings['way'].append(new['@id'])

    def _invert_relation_members(self, old: dict, new: dict, current: dict) -> None:
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

        print(f'💡 Performing DMP patch relation/{new["@id"]}')

        if patch := dmp_retry_reverse(old_members, new_members, current_members):
            current['member'] = [json.loads(p) for p in patch]
            print('✅ Patch successful')
            self.statistics['dmp:relation'] += 1
            self.statistics['dmp:relation:id'].append(new['@id'])
        else:
            # absolute delete
            create_diff = {m['@ref'] for m in ensure_iterable(new.get('member', []))}
            create_diff = create_diff.difference(m['@ref'] for m in ensure_iterable(old.get('member', [])))
            current['member'] = [m for m in ensure_iterable(current.get('member', [])) if m['@ref'] not in create_diff]

            self.statistics['dmp:fail:relation'] += 1
            self.statistics['dmp:fail:relation:id'].append(new['@id'])
            self.warnings['relation'].append(new['@id'])
