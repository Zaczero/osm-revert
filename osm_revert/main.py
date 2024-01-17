import functools
import os
import time
import traceback
from collections.abc import Sequence

import xmltodict

from osm_revert.config import CHANGESETS_LIMIT_CONFIG, CHANGESETS_LIMIT_MODERATOR_REVERT, CREATED_BY, WEBSITE
from osm_revert.diff_entry import DiffEntry
from osm_revert.invert import Inverter
from osm_revert.osm import OsmApi, build_osm_change
from osm_revert.overpass import Overpass
from osm_revert.utils import is_osm_moderator


def merge_and_sort_diffs(diffs: list[dict[str, list[DiffEntry]]]) -> dict[str, list[DiffEntry]]:
    result = diffs[0]

    for diff in diffs[1:]:
        for element_type, elements in diff.items():
            result[element_type] += elements

    for element_type, elements in result.items():
        # sort by newest edits first
        result[element_type] = sorted(elements, key=lambda t: t.timestamp, reverse=True)

    return result


def filter_discussion_changesets(changeset_ids: Sequence[int], target: str) -> Sequence[int]:
    if target == 'all':
        return changeset_ids
    if target == 'newest':
        return changeset_ids[-1:]
    if target == 'oldest':
        return changeset_ids[:1]

    print(f'🚧 Warning: Unknown discussion target: {target}')
    return ()


def print_warn_elements(warn_elements: dict[str, list[str]]) -> None:
    for element_type, element_ids in warn_elements.items():
        for element_id in element_ids:
            print(f'⚠️ Please verify: https://www.openstreetmap.org/{element_type}/{element_id}')


def main_timer(func):
    @functools.wraps(func)
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


# TODO: improved revert to date
# TODO: filter does not include nodes if way was unmodified
# https://overpass-api.de/achavi/?changeset=131696060
# https://www.openstreetmap.org/way/357241890/history


# TODO: Exit code: None ? (after success and total time)
# TODO: util function to ensure tags existence and type
# TODO: slow but very accurate revert (download full history of future edits); overpass-api: timeline
# TODO: dataclasses
@main_timer
def main(
    changeset_ids: Sequence[int],
    comment: str,
    username: str | None = None,
    password: str | None = None,
    *,
    oauth_token: dict | None = None,
    discussion: str | None = None,
    discussion_target: str | None = None,
    osc_file: str | None = None,
    print_osc: bool | None = None,
    query_filter: str = '',
    only_tags: Sequence[str] = (),
    fix_parents: bool = True,
) -> int:
    if not changeset_ids:
        raise ValueError('Missing changeset ids')

    changeset_ids = tuple(sorted(set(changeset_ids)))
    only_tags = frozenset(tag.strip() for tag in only_tags if tag)

    if not username and not password:
        username = os.getenv('OSM_USERNAME')
        password = os.getenv('OSM_PASSWORD')

    print('🔒️ Logging in to OpenStreetMap')
    osm = OsmApi(username=username, password=password, oauth_token=oauth_token)
    user = osm.get_authorized_user()

    user_edits = user['changesets']['count']
    user_is_moderator = is_osm_moderator(user['roles'])

    print(f'👤 Welcome, {user["display_name"]}{" 🔷" if user_is_moderator else ""}!')

    changesets_limit_config = CHANGESETS_LIMIT_CONFIG['moderator' if user_is_moderator else '']
    changesets_limit = max(v for k, v in changesets_limit_config.items() if k <= user_edits)

    if changesets_limit == 0:
        min_edits = min(k for k in changesets_limit_config if k > 0)
        print(f'🐥 You need to make at least {min_edits} edits to use this tool')
        return -1

    if changesets_limit < len(changeset_ids):
        print(f'🛟 For safety, you can only revert up to {changesets_limit} changesets at a time')

        if limit_increase := min((k for k in changesets_limit_config if k > user_edits), default=None):
            print(f'🛟 To increase this limit, make at least {limit_increase} edits')

        return -1

    overpass = Overpass()
    diffs = []

    for changeset_id in changeset_ids:
        print(f'☁️ Downloading changeset {changeset_id}')

        print('[1/?] OpenStreetMap …')
        changeset = osm.get_changeset(changeset_id)

        if user_edits < CHANGESETS_LIMIT_MODERATOR_REVERT and not user_is_moderator:
            changeset_user = osm.get_user(changeset['osm']['changeset']['@uid'])
            if changeset_user and is_osm_moderator(changeset_user['roles']):
                print('🛑 Moderators changesets cannot be reverted')
                return -1

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

            if diff_size > changeset_size:
                raise RuntimeError(f'Diff must not be larger than changeset size: {diff_size=}, {changeset_size=}')

            if query_filter:
                print(f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""} (🪣 filtered)')
            else:
                print(f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""}')

    print('🔁 Generating a revert')
    merged_diffs = merge_and_sort_diffs(diffs)

    inverter = Inverter(only_tags)
    invert = inverter.invert_diff(merged_diffs)
    parents_counter = overpass.update_parents(invert, fix_parents=fix_parents)

    if parents_counter:
        if fix_parents:
            print(f'🛠️ Fixing {parents_counter} parent{"s" if parents_counter > 1 else ""}')
        else:
            print(f'🛠️ Skipping {parents_counter} element{"s" if parents_counter > 1 else ""} (not orphaned)')

    invert_size = sum(len(elements) for elements in invert.values())

    if invert_size == 0:
        print('✅ Nothing to revert')
        return 0

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

        print_warn_elements(inverter.warnings)
        print('✅ Success')
        return 0

    else:
        changeset_max_size = osm.get_changeset_max_size()

        if invert_size > changeset_max_size:
            print(f'🐘 Revert is too big: {invert_size} > {changeset_max_size}')

            if len(changeset_ids) > 1:
                print('🐘 Hint: Try reducing the amount of changesets to revert at once')

            if fix_parents:
                print('🐘 Hint: Try disabling parent fixing')

            return -1

        print(f'🌍️ Uploading {invert_size} change{"s" if invert_size > 1 else ""}')

        extra_args = {'changesets_count': user_edits + 1, 'created_by': CREATED_BY, 'website': WEBSITE}

        if len(changeset_ids) == 1:
            extra_args['id'] = ';'.join(f'https://www.openstreetmap.org/changeset/{c}' for c in changeset_ids)
        else:
            extra_args['id'] = ';'.join(map(str, changeset_ids))

        if query_filter:
            extra_args['filter'] = query_filter

        if changeset_id := osm.upload_diff(invert, comment, extra_args | inverter.statistics):
            changeset_url = f'https://www.openstreetmap.org/changeset/{changeset_id}'

            discussion = discussion.strip()

            if len(discussion) >= 4:  # prevent accidental discussions
                discussion += f'\n\n{changeset_url}'

                discuss_changeset_ids = filter_discussion_changesets(changeset_ids, discussion_target)
                print(
                    f'💬 Discussing {len(discuss_changeset_ids)} changeset{"s" if len(discuss_changeset_ids) > 1 else ""}'
                )

                for i, changeset_id in enumerate(discuss_changeset_ids, 1):
                    status = osm.post_discussion_comment(changeset_id, discussion)
                    print(f'[{i}/{len(discuss_changeset_ids)}] Changeset {changeset_id}: {status}')

            print_warn_elements(inverter.warnings)
            print('✅ Success')
            print(f'✅ {changeset_url}')
            return 0

    return -1


if __name__ == '__main__':
    # For debugging
    main(
        changeset_ids=[142945620],
        comment='revert',
        print_osc=True,
        query_filter='',
        fix_parents=True,
    )
