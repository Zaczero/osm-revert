import os

VERSION = '1.1'

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
    },
    'moderator': {
        0: 50
    }
}

TAG_MAX_LENGTH = 255
TAG_PREFIX = 'revert'

NO_TAG_PREFIX = {'comment', 'created_by', 'website'}
