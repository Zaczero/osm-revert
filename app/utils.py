from typing import Iterable, Optional

from diff_match_patch import diff_match_patch


def ensure_iterable(item) -> Iterable:
    if isinstance(item, list):
        return item

    return item,


def dmp_retry_reverse(old: list, new: list, current: list) -> Optional[list]:
    if result := dmp(old, new, current):
        return result

    return dmp(old, new[::-1], current)


def dmp(old: list, new: list, current: list) -> Optional[list]:
    old_lines = '\n'.join(old) + '\n'
    new_lines = '\n'.join(new) + '\n'
    current_lines = '\n'.join(current) + '\n'

    d = diff_match_patch()
    d.Match_Threshold = 1
    d.Patch_DeleteThreshold = 0
    diff = d.diff_lineMode(new_lines, current_lines, deadline=None)
    d.diff_cleanupSemanticLossless(diff)
    patch = d.patch_make(diff)

    result_lines, result_bools = d.patch_apply(patch, old_lines)

    # some patches failed to apply
    if not all(result_bools):
        return None

    result = result_lines.strip().split('\n')

    # result must not contain duplicates
    if len(result) != len(set(result)):
        return None

    # result must not delete any common elements
    if set(old).intersection(current) - set(result):
        return None

    # result must not create any new elements
    if set(result) - set(old).union(current):
        return None

    return result
