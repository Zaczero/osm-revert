from typing import Iterable, Optional

from diff_match_patch import diff_match_patch


def ensure_iterable(item) -> Iterable:
    if isinstance(item, list):
        return item

    return item,


def dmp(old: list, new: list, current: list) -> Optional[list]:
    old_lines = '\n'.join(old) + '\n'
    new_lines = '\n'.join(new) + '\n'
    current_lines = '\n'.join(current) + '\n'

    d = diff_match_patch()
    d.Match_Threshold = 1
    d.Match_Distance = len(old_lines)
    diff = d.diff_lineMode(new_lines, current_lines, deadline=None)
    d.diff_cleanupSemanticLossless(diff)
    patch = d.patch_make(diff)

    result_lines, result_bools = d.patch_apply(patch, old_lines)

    # some patches failed to apply
    if not all(result_bools):
        return None

    result = result_lines.strip().split('\n')

    # the result contains duplicates (that's bad)
    if len(result) != len(set(result)):
        return None

    # result must contain all common nodes in old + current
    if set(old).intersection(set(current)) - set(result):
        return None

    return result
