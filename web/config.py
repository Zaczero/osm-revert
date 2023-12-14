import os

VERSION = '1.1.2'
CREATED_BY = f'osm-revert-ui {VERSION}'
USER_AGENT = f'osm-revert-ui/{VERSION} (+https://github.com/Zaczero/osm-revert)'

INSTANCE_SECRET = os.getenv('INSTANCE_SECRET')

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
