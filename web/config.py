import os

import sentry_sdk

VERSION = '1.2.5'
VERSION_DATE = ''

if VERSION_DATE:
    VERSION += f'.{VERSION_DATE}'

CREATED_BY = f'osm-revert-ui {VERSION}'
USER_AGENT = f'osm-revert-ui/{VERSION} (+https://github.com/Zaczero/osm-revert)'

INSTANCE_SECRET = os.getenv('INSTANCE_SECRET')

TEST_ENV = os.getenv('TEST_ENV', '0').strip().lower() in ('1', 'true', 'yes')

if TEST_ENV:
    print('[CONF] Running in test environment')

OSM_CLIENT = os.getenv('OSM_CLIENT', None)
OSM_SECRET = os.getenv('OSM_SECRET', None)
OSM_SCOPES = 'read_prefs write_api'

if not OSM_CLIENT or not OSM_SECRET:
    print(
        '🚧 Warning: '
        'Environment variables OSM_CLIENT and/or OSM_SECRET are not set. '
        'You will not be able to authenticate with OpenStreetMap.'
    )

CONNECTION_LIMIT = int(os.getenv('CONNECTION_LIMIT', 2))

if not TEST_ENV:
    sentry_sdk.init(
        dsn='https://1351a3b285a2942f67f0a13daa66ca99@sentry.monicz.dev/5',
        release=VERSION,
        enable_tracing=True,
        traces_sample_rate=0.2,
        trace_propagation_targets=None,
        profiles_sample_rate=0.2,
    )
