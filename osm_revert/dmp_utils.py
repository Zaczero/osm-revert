from collections.abc import Collection, Sequence
from typing import cast

from osm_revert.context_logger import context_print
from osm_revert.diff_match_patch import diff_match_patch


def dmp_retry_reverse(old: Collection, new: Sequence, current: Collection) -> list[str] | None:
    if result := dmp(old, new, current):
        return result
    context_print('[DMP] Retrying in reverse')
    return dmp(old, new[::-1], current)


def dmp(old: Collection, new: Collection, current: Collection) -> list[str] | None:
    old_lines = '\n'.join(old) + '\n'
    new_lines = '\n'.join(new) + '\n'
    current_lines = '\n'.join(current) + '\n'

    d = diff_match_patch()
    d.Match_Threshold = 1
    d.Patch_DeleteThreshold = 0

    (old_text, new_text, current_text, line_arr) = d.diff_linesToChars(old_lines, new_lines, current_lines)
    diff = d.diff_main(new_text, current_text, checklines=False)
    patch = d.patch_make(diff)

    result_text, result_bools = d.patch_apply(patch, old_text)

    # some patches failed to apply
    if not all(result_bools):
        context_print('[DMP] Patch failed (not_all)')
        return None

    result_lines = cast(str, d.diff_charsToLinesText(result_text, line_arr))
    result = result_lines.strip().split('\n')

    # result must not contain duplicates
    if len(result) != len(set(result)):
        context_print('[DMP] Patch failed (duplicate)')
        return None

    result_set = set(result)
    old_set = set(old)

    # result must not create any new elements
    if result_set - old_set.union(current):
        context_print('[DMP] Patch failed (create_new)')
        return None

    # result must not delete any common elements
    if old_set.intersection(current) - result_set:
        context_print('[DMP] Patch failed (common_delete)')
        return None

    return result
