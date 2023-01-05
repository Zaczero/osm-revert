from typing import Iterable


def ensure_iterable(item) -> Iterable:
    if isinstance(item, list):
        return item

    return item,
