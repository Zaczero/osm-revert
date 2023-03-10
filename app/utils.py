import functools
from typing import Optional, Sized

from requests import Session

from config import USER_AGENT
from diff_match_patch import diff_match_patch


def ensure_iterable(item) -> list | tuple:
    if item is None:
        return []

    if isinstance(item, list) or isinstance(item, tuple):
        return item

    return [item]


def dmp_retry_reverse(old: list, new: list, current: list) -> Optional[list]:
    if result := dmp(old, new, current):
        return result

    print('[DMP] Retrying in reverse')
    return dmp(old, new[::-1], current)


def dmp(old: list, new: list, current: list) -> Optional[list]:
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
        print('[DMP] Patch failed (not_all)')
        return None

    result_lines = d.diff_charsToLinesText(result_text, line_arr)
    result = result_lines.strip().split('\n')

    # result must not contain duplicates
    if len(result) != len(set(result)):
        print('[DMP] Patch failed (duplicate)')
        return None

    # result must not create any new elements
    if set(result) - set(old).union(current):
        print('[DMP] Patch failed (create_new)')
        return None

    # result must not delete any common elements
    if set(old).intersection(current) - set(result):
        print('[DMP] Patch failed (common_delete)')
        return None

    return result


def get_http_client(*, auth: Optional = None, headers: Optional[dict] = None):
    if not headers:
        headers = {}

    s = Session()
    s.auth = auth
    s.headers.update({'User-Agent': USER_AGENT} | headers)
    s.request = functools.partial(s.request, timeout=30)

    return s
