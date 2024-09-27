import os

import sentry_sdk
from githead import githead

VERSION = 'git#' + githead()[:7]
WEBSITE = os.getenv('OSM_REVERT_WEBSITE')
CREATED_BY = f'osm-revert {VERSION}'
USER_AGENT = f'osm-revert/{VERSION} (+https://github.com/Zaczero/osm-revert)'

TEST_ENV = os.getenv('TEST_ENV', '0').strip().lower() in ('1', 'true', 'yes')
if TEST_ENV:
    print('[CONF] Running in test environment')

CHANGESETS_LIMIT_CONFIG = {
    '': {
        0: 0,
        10: 3,
        100: 5,
        500: 10,
        4000: 30,
    },
    'moderator': {0: 50},
}

TAG_MAX_LENGTH = 255
TAG_PREFIX = 'revert'
NO_TAG_PREFIX = {'comment', 'changesets_count', 'created_by', 'host', 'website'}

REVERT_TO_DATE = os.getenv('REVERT_TO_DATE', None)
CHANGESETS_LIMIT_MODERATOR_REVERT = int(os.getenv('CHANGESETS_LIMIT_MODERATOR_REVERT', 2000))

OSM_URL = os.getenv('OSM_URL', 'https://www.openstreetmap.org')
OSM_API_URL = os.getenv('OSM_API_URL', 'https://api.openstreetmap.org')
OVERPASS_URLS = os.getenv(
    'OVERPASS_URLS', 'https://overpass.monicz.dev/api/interpreter https://overpass-api.de/api/interpreter'
).split()

OSM_CLIENT = os.getenv('OSM_CLIENT')
OSM_SECRET = os.getenv('OSM_SECRET')
OSM_SCOPES = 'read_prefs write_api'
CONNECTION_LIMIT = int(os.getenv('CONNECTION_LIMIT', 2))

if SENTRY_DSN := os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=VERSION,
        environment=OSM_URL,
        enable_tracing=True,
        traces_sample_rate=0.5,
        trace_propagation_targets=None,
    )
