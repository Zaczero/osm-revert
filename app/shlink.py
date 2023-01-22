import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

from config import CREATED_BY
from utils import get_http_client

SHLINK_ENDPOINT = os.getenv('SHLINK_ENDPOINT', None)
SHLINK_API_KEY = os.getenv('SHLINK_API_KEY', None)
SHLINK_EXPIRE = timedelta(days=30)


class Shlink:
    @property
    def available(self) -> bool:
        return SHLINK_ENDPOINT and SHLINK_API_KEY

    def shorten(self, url: str) -> str:
        with get_http_client() as c:
            r = c.post(SHLINK_ENDPOINT + f'/rest/v3/short-urls', json={
                'longUrl': url,
                'validSince': datetime.now().replace(microsecond=0).astimezone().isoformat(),
                'validUntil': (datetime.now() + SHLINK_EXPIRE).replace(microsecond=0).astimezone().isoformat(),
                'validateUrl': False,
                'tags': [CREATED_BY],
                'crawlable': False,
                'forwardQuery': False,
                'findIfExists': True,
                'domain': urlparse(SHLINK_ENDPOINT).netloc
            }, headers={
                'X-Api-Key': SHLINK_API_KEY
            })
            r.raise_for_status()

        return r.json()['shortUrl']
