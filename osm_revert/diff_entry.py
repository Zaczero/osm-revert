from typing import NamedTuple


class DiffEntry(NamedTuple):
    timestamp: int
    element_id: str
    element_old: dict
    element_new: dict
    element_current: dict
