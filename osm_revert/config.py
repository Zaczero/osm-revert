from os import environ, getenv
from urllib.parse import urlsplit

import sentry_sdk
from githead import githead
from pydantic import SecretStr
from sentry_sdk.integrations.pure_eval import PureEvalIntegration

VERSION = 'git#' + githead()[:7]
WEBSITE = getenv('OSM_REVERT_WEBSITE')
CREATED_BY = f'osm-revert {VERSION}'
USER_AGENT = f'osm-revert/{VERSION} (+https://github.com/Zaczero/osm-revert)'

TEST_ENV = getenv('TEST_ENV', '0').strip().lower() in ('1', 'true', 'yes')
if TEST_ENV:
    print('[CONF] Running in test environment')

CHANGESETS_LIMIT_CONFIG = {
    '': {
        0: 0,
        10: 1,
        100: 3,
        500: 10,
        3000: 30,
    },
    'moderator': {0: 50},
}

TAG_MAX_LENGTH = 255
TAG_PREFIX = 'revert'
NO_TAG_PREFIX = {'comment', 'changesets_count', 'created_by', 'host', 'website'}

REVERT_TO_DATE = getenv('REVERT_TO_DATE', None)
CHANGESETS_LIMIT_MODERATOR_REVERT = int(getenv('CHANGESETS_LIMIT_MODERATOR_REVERT', 2000))

OSM_URL = getenv('OSM_URL', 'https://www.openstreetmap.org')
OSM_API_URL = getenv('OSM_API_URL', 'https://api.openstreetmap.org')
OVERPASS_URL = getenv('OVERPASS_URL') or getenv('OVERPASS_URLS', 'https://overpass-api.de/api').split()[0]
OVERPASS_RESPONSE_MAX_SIZE = int(getenv('OVERPASS_RESPONSE_MAX_SIZE', 50 * 1024 * 1024))  # 50MB

OSM_CLIENT = environ['OSM_CLIENT']
OSM_SECRET = SecretStr(environ['OSM_SECRET'])
OSM_SCOPES = 'read_prefs write_api'
CONNECTION_LIMIT = int(getenv('CONNECTION_LIMIT', 2))

if SENTRY_DSN := getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=VERSION,
        environment=urlsplit(OSM_URL).hostname,
        enable_tracing=True,
        traces_sample_rate=0.5,
        trace_propagation_targets=None,
        profiles_sample_rate=0.5,
        integrations=(PureEvalIntegration(),),
        _experiments={'continuous_profiling_auto_start': True},
    )
