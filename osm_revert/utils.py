import asyncio
import os
import random
import ssl
import time
from collections.abc import Iterable
from functools import wraps

from httpx import AsyncClient

from osm_revert.config import USER_AGENT
from osm_revert.context_logger import context_print

_SSL_CONTEXT = ssl.create_default_context(cafile=os.environ['SSL_CERT_FILE'])


def get_http_client(base_url: str, *, headers: dict | None = None) -> AsyncClient:
    if headers is None:
        headers = {}
    return AsyncClient(
        base_url=base_url,
        follow_redirects=True,
        timeout=30,
        headers={'User-Agent': USER_AGENT, **headers},
        verify=_SSL_CONTEXT,
    )


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


def is_osm_moderator(roles: Iterable[str]) -> bool:
    return bool({'moderator', 'administrator'}.intersection(roles))
