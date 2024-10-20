import asyncio
import random
import time
from collections.abc import Collection
from functools import lru_cache, wraps

from httpx import AsyncClient

from osm_revert.config import USER_AGENT
from osm_revert.context_logger import context_print


def retry_exponential(func):
    timeout = 10
    start = 1

    @wraps(func)
    async def wrapper(*args, **kwargs):
        ts = time.perf_counter()
        sleep = start

        while True:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if (time.perf_counter() + sleep) - ts > timeout:
                    context_print(f'[⛔] {func.__name__} failed')
                    raise e
                await asyncio.sleep(sleep)
                sleep = min(sleep * (1 + random.random()), 1800)  # max 30 minutes  # noqa: S311

    return wrapper


def ensure_iterable(item) -> list | tuple:
    if item is None:
        return ()
    if isinstance(item, list | tuple):
        return item
    return (item,)


def get_http_client(base_url: str = '', *, headers: dict | None = None) -> AsyncClient:
    if headers is None:
        headers = {}
    return AsyncClient(
        base_url=base_url,
        headers={'User-Agent': USER_AGENT, **headers},
        timeout=30,
        follow_redirects=True,
    )


@lru_cache(maxsize=128)
def is_osm_moderator(roles: Collection[str]) -> bool:
    return bool({'moderator', 'administrator'}.intersection(roles))
