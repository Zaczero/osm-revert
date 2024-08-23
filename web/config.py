import os

import sentry_sdk

VERSION = '1.4.0'
VERSION_DATE = ''

if VERSION_DATE:
    VERSION += f'.{VERSION_DATE}'

CREATED_BY = f'osm-revert-ui {VERSION}'
USER_AGENT = f'osm-revert-ui/{VERSION} (+https://github.com/Zaczero/osm-revert)'

TEST_ENV = os.getenv('TEST_ENV', '0').strip().lower() in ('1', 'true', 'yes')

if TEST_ENV:
    print('[CONF] Running in test environment')

OSM_CLIENT = os.environ['OSM_CLIENT']
OSM_SECRET = os.environ['OSM_SECRET']
OSM_SCOPES = 'read_prefs write_api'

CONNECTION_LIMIT = int(os.getenv('CONNECTION_LIMIT', 2))

if SENTRY_DSN := os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=VERSION,
        enable_tracing=True,
        traces_sample_rate=0.5,
        trace_propagation_targets=None,
    )
