import ssl
from functools import cache

import certifi
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from config import USER_AGENT


@cache
def http() -> ClientSession:
    return ClientSession(
        connector=TCPConnector(
            ssl=ssl.create_default_context(cafile=certifi.where()),
            ttl_dns_cache=600,
        ),
        headers={'User-Agent': USER_AGENT},
        timeout=ClientTimeout(total=15, connect=10),
    )
