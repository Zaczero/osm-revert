import os

VERSION = '1.3.6'

if version_date := os.getenv('OSM_REVERT_VERSION_DATE'):
    VERSION += f'.{version_date}'

if version_suffix := os.getenv('OSM_REVERT_VERSION_SUFFIX'):
    VERSION += f'-{version_suffix}'

WEBSITE = os.getenv('OSM_REVERT_WEBSITE', None)
CREATED_BY = f'osm-revert {VERSION}'
USER_AGENT = f'osm-revert/{VERSION} (+https://github.com/Zaczero/osm-revert)'

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
NO_TAG_PREFIX = {'comment', 'changesets_count', 'created_by', 'website'}

XML_HEADERS = {'Content-Type': 'text/xml; charset=utf-8'}

REVERT_TO_DATE = os.getenv('REVERT_TO_DATE', None)
CHANGESETS_LIMIT_MODERATOR_REVERT = int(os.getenv('CHANGESETS_LIMIT_MODERATOR_REVERT', 2000))
