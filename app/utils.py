from collections import defaultdict
from typing import Any

from httpx import Client

from config import USER_AGENT
from diff_match_patch import diff_match_patch

_RUN_COUNTER = defaultdict(int)


def limit_execution_count(name: str, limit: int) -> bool:
    if _RUN_COUNTER[name] >= limit:
        return True

    _RUN_COUNTER[name] += 1

    if _RUN_COUNTER[name] >= limit:
        print(f'🔇 Suppressing further messages for {name!r}')

    return False


def ensure_iterable(item) -> list | tuple:
    if item is None:
        return []

    if isinstance(item, list) or isinstance(item, tuple):
        return item

    return [item]


def dmp_retry_reverse(old: list, new: list, current: list) -> list | None:
    if result := dmp(old, new, current):
        return result

    print('[DMP] Retrying in reverse')
    return dmp(old, new[::-1], current)


def dmp(old: list, new: list, current: list) -> list | None:
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


def get_http_client(base_url: str = '', *, auth: Any | None = None, headers: dict | None = None) -> Client:
    if not headers:
        headers = {}

    return Client(
        base_url=base_url,
        auth=auth,
        headers={'User-Agent': USER_AGENT} | headers,
        timeout=30,
        follow_redirects=True,
    )
