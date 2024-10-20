import os
import time
import traceback
from collections.abc import Sequence
from functools import wraps

import uvloop
import xmltodict

from osm_revert.config import CHANGESETS_LIMIT_CONFIG, CHANGESETS_LIMIT_MODERATOR_REVERT, CREATED_BY, OSM_URL, WEBSITE
from osm_revert.context_logger import context_print
from osm_revert.diff_entry import DiffEntry
from osm_revert.invert import Inverter
from osm_revert.osm import OsmApi, build_osm_change
from osm_revert.overpass import Overpass
from osm_revert.utils import is_osm_moderator


def merge_and_sort_diffs(diffs: Sequence[dict[str, list[DiffEntry]]]) -> dict[str, list[DiffEntry]]:
    if not diffs:
        return {}

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
    context_print(f'🚧 Warning: Unknown discussion target: {target}')
    return ()


def print_warn_elements(warn_elements: dict[str, list[str]]) -> None:
    for element_type, element_ids in warn_elements.items():
        for element_id in element_ids:
            context_print(f'⚠️ Please verify: {OSM_URL}/{element_type}/{element_id}')


def main_timer(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.perf_counter()

        try:
            exit_code = await func(*args, **kwargs)
        except Exception:
            context_print(traceback.format_exc())
            exit_code = -2

        total_time = time.perf_counter() - start_time
        context_print(f'🏁 Total time: {total_time:.1F} sec')
        return exit_code

    return wrapper


# TODO: improved revert to date
# TODO: filter does not include nodes if way was unmodified
# https://overpass-api.de/achavi/?changeset=131696060
# https://www.openstreetmap.org/way/357241890/history


# TODO: util function to ensure tags existence and type
# TODO: slow but very accurate revert (download full history of future edits); overpass-api: timeline
# TODO: dataclasses
@main_timer
async def main(
    changeset_ids: Sequence[int],
    comment: str,
    *,
    osm_token: str,
    discussion: str = '',
    discussion_target: str = 'all',
    osc_file: str | None = None,
    print_osc: bool | None = None,
    query_filter: str = '',
    only_tags: Sequence[str] = (),
    fix_parents: bool = True,
) -> int:
    if not changeset_ids:
        raise ValueError('Missing changeset ids')

    changeset_ids = tuple(sorted(set(changeset_ids)))
    only_tags_set = frozenset(tag.strip() for tag in only_tags if tag)

    context_print('🔒️ Logging in to OpenStreetMap')
    osm = OsmApi(osm_token)
    user = await osm.get_authorized_user()

    user_edits = user['changesets']['count']
    user_is_moderator = is_osm_moderator(user['roles'])

    context_print(f'👤 Welcome, {user["display_name"]}{" 🔷" if user_is_moderator else ""}!')

    changesets_limit_config = CHANGESETS_LIMIT_CONFIG['moderator' if user_is_moderator else '']
    changesets_limit = max(v for k, v in changesets_limit_config.items() if k <= user_edits)

    if changesets_limit == 0:
        min_edits = min(k for k in changesets_limit_config if k > 0)
        context_print(f'🐥 You need to make at least {min_edits} edits to use this tool')
        return -1

    if changesets_limit < len(changeset_ids):
        context_print(f'🛟 For safety, you can only revert up to {changesets_limit} changesets at a time')

        if limit_increase := min((k for k in changesets_limit_config if k > user_edits), default=None):
            context_print(f'🛟 To increase this limit, make at least {limit_increase} edits')

        return -1

    overpass = Overpass()
    diffs = []

    for changeset_id in changeset_ids:
        context_print(f'☁️ Downloading changeset {changeset_id}')

        context_print('[1/?] OpenStreetMap …')
        changeset = await osm.get_changeset(changeset_id)

        if user_edits < CHANGESETS_LIMIT_MODERATOR_REVERT and not user_is_moderator:
            changeset_user = await osm.get_user(changeset['osm']['changeset']['@uid'])
            if changeset_user and is_osm_moderator(changeset_user['roles']):
                context_print('🛑 Moderators changesets cannot be reverted')
                return -1

        changeset_size = sum(len(v) for p in changeset['partition'].values() for v in p.values())
        partition_count = len(changeset['partition'])
        steps = partition_count + 1

        context_print(f'[1/{steps}] OpenStreetMap: {changeset_size} element{"s" if changeset_size > 1 else ""}')

        if changeset_size:
            if partition_count > 2:
                context_print(f'[2/{steps}] Overpass ({partition_count} partitions, this may take a while) …')
            else:
                context_print(
                    f'[2/{steps}] Overpass ({partition_count} partition{"s" if partition_count > 1 else ""}) …'
                )

            diff = await overpass.get_changeset_elements_history(changeset, steps, query_filter)
            if not diff:
                return -1

            diffs.append(diff)
            diff_size = sum(len(el) for el in diff.values())

            if diff_size > changeset_size:
                raise RuntimeError(f'Diff must not be larger than changeset size: {diff_size=}, {changeset_size=}')

            if query_filter:
                context_print(
                    f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""} (🪣 filtered)'
                )
            else:
                context_print(f'[{steps}/{steps}] Overpass: {diff_size} element{"s" if diff_size > 1 else ""}')

    context_print('🔁 Generating a revert')
    merged_diffs = merge_and_sort_diffs(diffs)

    inverter = Inverter(only_tags_set)
    invert = inverter.invert_diff(merged_diffs)

    parents_counter = await overpass.update_parents(invert, fix_parents=fix_parents)
    if parents_counter:
        if fix_parents:
            context_print(f'🛠️ Fixing {parents_counter} parent{"s" if parents_counter > 1 else ""}')
        else:
            context_print(f'🛠️ Skipping {parents_counter} element{"s" if parents_counter > 1 else ""} (not orphaned)')

    invert_size = sum(len(elements) for elements in invert.values())
    if invert_size == 0:
        context_print('✅ Nothing to revert')
        return 0

    if osc_file or print_osc:
        context_print(f'💾 Saving {invert_size} change{"s" if invert_size > 1 else ""} to .osc')
        osm_change = build_osm_change(invert, changeset_id=None)
        osm_change_xml = xmltodict.unparse(osm_change, pretty=True)

        if osc_file:
            with open(osc_file, 'w', encoding='utf-8') as f:
                f.write(osm_change_xml)

        if print_osc:
            context_print('<osc>')
            context_print(osm_change_xml)
            context_print('</osc>')

        print_warn_elements(inverter.warnings)
        context_print('✅ Success')
        return 0

    else:
        changeset_max_size = await osm.get_changeset_max_size()

        if invert_size > changeset_max_size:
            context_print(f'🐘 Revert is too big: {invert_size} > {changeset_max_size}')
            if len(changeset_ids) > 1:
                context_print('🐘 Hint: Try reducing the amount of changesets to revert at once')
            if fix_parents:
                context_print('🐘 Hint: Try disabling parent fixing')
            return -1

        context_print(f'🌍️ Uploading {invert_size} change{"s" if invert_size > 1 else ""}')
        extra_args = {'changesets_count': user_edits + 1, 'created_by': CREATED_BY, 'host': WEBSITE}

        if len(changeset_ids) == 1:
            extra_args['id'] = ';'.join(f'{OSM_URL}/changeset/{c}' for c in changeset_ids)
        else:
            extra_args['id'] = ';'.join(map(str, changeset_ids))

        if query_filter:
            extra_args['filter'] = query_filter

        if changeset_id := await osm.upload_diff(invert, comment, extra_args | inverter.statistics):
            changeset_url = f'{OSM_URL}/changeset/{changeset_id}'

            discussion = discussion.strip()

            if len(discussion) >= 4:  # prevent accidental discussions
                discussion += f'\n\n{changeset_url}'

                discuss_changeset_ids = filter_discussion_changesets(changeset_ids, discussion_target)
                context_print(
                    f'💬 Discussing {len(discuss_changeset_ids)} changeset{"s" if len(discuss_changeset_ids) > 1 else ""}'
                )

                for i, changeset_id in enumerate(discuss_changeset_ids, 1):
                    status = await osm.post_discussion_comment(changeset_id, discussion)
                    context_print(f'[{i}/{len(discuss_changeset_ids)}] Changeset {changeset_id}: {status}')

            print_warn_elements(inverter.warnings)
            context_print('✅ Success')
            context_print(f'✅ {changeset_url}')
            return 0

    return -1


if __name__ == '__main__':
    # For debugging
    uvloop.run(
        main(
            changeset_ids=[124750619],
            comment='revert',
            print_osc=True,
            query_filter='',
            fix_parents=True,
            osm_token=os.environ['OSM_TOKEN'],
        )
    )
