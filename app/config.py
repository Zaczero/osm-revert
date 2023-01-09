import os

VERSION = '1.0'

if version_suffix := os.getenv('OSM_REVERT_VERSION_SUFFIX'):
    VERSION += f'-{version_suffix}'

WEBSITE = os.getenv('OSM_REVERT_WEBSITE', None)
CREATED_BY = f'osm-revert {VERSION}'
USER_AGENT = f'osm-revert/{VERSION} (+https://github.com/Zaczero/osm-revert)'

USER_MIN_EDITS = 10

TAG_MAX_LENGTH = 255
TAG_PREFIX = 'revert'

NO_TAG_PREFIX = {'comment', 'created_by', 'website'}
