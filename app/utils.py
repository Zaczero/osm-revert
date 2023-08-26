import functools
import random
import time
from collections import defaultdict
from datetime import timedelta
from math import inf
from typing import Any

from httpx import Client

from config import USER_AGENT

_RUN_COUNTER = defaultdict(int)


def limit_execution_count(name: str, limit: int) -> bool:
    _RUN_COUNTER[name] += 1

    if _RUN_COUNTER[name] == limit + 1:
        print(f'🔇 Suppressing further messages for {name!r}')

    return _RUN_COUNTER[name] > limit


def retry_exponential(timeout: timedelta | float | None = 10, *, start: float = 1):
    if timeout is None:
        timeout_seconds = inf
    elif isinstance(timeout, timedelta):
        timeout_seconds = timeout.total_seconds()
    else:
        timeout_seconds = timeout

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            ts = time.perf_counter()
            sleep = start

            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if (time.perf_counter() + sleep) - ts > timeout_seconds:
                        print(f'[⛔] {func.__name__} failed')
                        raise e
                    time.sleep(sleep)
                    sleep = min(sleep * (1 + random.random()), 1800)  # max 30 minutes

        return wrapper
    return decorator


def ensure_iterable(item) -> list | tuple:
    if item is None:
        return []

    if isinstance(item, list) or isinstance(item, tuple):
        return item

    return [item]


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
