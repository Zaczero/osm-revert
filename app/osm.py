import xmltodict
from authlib.integrations.httpx_client import OAuth2Auth

from config import (CREATED_BY, NO_TAG_PREFIX, TAG_MAX_LENGTH, TAG_PREFIX,
                    XML_HEADERS)
from utils import ensure_iterable, get_http_client, retry_exponential


def sort_relations_for_osm_change(relations: list[dict]) -> list[dict]:
    change_ids = {rel['@id'] for rel in relations}

    # tuples: (relation, set of relation ids it depends on)
    dependency_state = {
        rel['@id']: (rel, set(
            m['@ref'] for m in ensure_iterable(rel.get('member', []))
            if m['@type'] == 'relation'
        ).intersection(change_ids))
        for rel in relations
    }

    no_dependencies = []

    for rel_id, (rel, deps) in tuple(dependency_state.items()):
        if not deps:
            no_dependencies.append(rel)
            dependency_state.pop(rel_id)

    result = []
    hidden = []

    while no_dependencies:
        rel = no_dependencies.pop()
        rel_id = rel['@id']

        if rel['@visible'] == 'true':
            result.append(rel)
        else:
            hidden.append(rel)

        for other_rel_id, (other_rel, deps) in tuple(dependency_state.items()):
            if rel_id in deps:
                deps.remove(rel_id)

                if not deps:
                    no_dependencies.append(other_rel)
                    dependency_state.pop(other_rel_id)

    # delete relations with most dependencies first
    result.extend(reversed(hidden))

    for rel, deps in dependency_state.values():
        print(f'🚧 Warning: relation/{rel["@id"]} has {len(deps)} circular dependencies')
        result.append(rel)

    return result


def build_osm_change(diff: dict, changeset_id: str | None) -> dict:
    result = {'osmChange': {
        '@version': 0.6,
        '@generator': CREATED_BY,
        'modify': {
            'node': [],
            'way': [],
            'relation': []
        },
        'delete': {
            'relation': [],
            'way': [],
            'node': []
        }}}

    for element_type, elements in diff.items():
        if element_type == 'relation':
            elements = sort_relations_for_osm_change(elements)

        for element in elements:
            if changeset_id:
                element['@changeset'] = changeset_id
            else:
                del element['@changeset']

            if element['@visible'] == 'true':
                action = 'modify'
            else:
                action = 'delete'
                element.pop('@lat', None)
                element.pop('@lon', None)
                element.pop('tag', None)
                element.pop('nd', None)
                element.pop('member', None)

            result['osmChange'][action][element_type].append(element)

    return result


class OsmApi:
    def __init__(self, *,
                 username: str = None, password: str = None,
                 oauth_token: dict = None):
        if oauth_token:
            auth = OAuth2Auth(oauth_token)
        elif username and password:
            auth = (username, password)
        else:
            raise Exception('Authorization is required')

        self._http = get_http_client('https://api.openstreetmap.org/api', auth=auth)

    @retry_exponential()
    def get_changeset_max_size(self) -> int:
        r = self._http.get('/capabilities')
        r.raise_for_status()

        caps = xmltodict.parse(r.text)

        return int(caps['osm']['api']['changesets']['@maximum_elements'])

    @retry_exponential()
    def get_authorized_user(self) -> dict:
        r = self._http.get('/0.6/user/details.json')
        r.raise_for_status()

        return r.json()['user']

    @retry_exponential()
    def get_changeset(self, changeset_id: int) -> dict:
        info_resp = self._http.get(f'/0.6/changeset/{changeset_id}')
        info_resp.raise_for_status()

        diff_resp = self._http.get(f'/0.6/changeset/{changeset_id}/download')
        diff_resp.raise_for_status()

        info = xmltodict.parse(info_resp.text)
        diff = xmltodict.parse(diff_resp.text)
        diff['partition'] = {}

        for action_type, affected_elements in diff['osmChange'].items():
            if action_type.startswith('@'):
                continue

            new = {
                'node': [],
                'way': [],
                'relation': []
            }

            for affected_element in ensure_iterable(affected_elements):
                element_type, element = next(iter(affected_element.items()))

                new[element_type].append(element)

                if element['@timestamp'] not in diff['partition']:
                    diff['partition'][element['@timestamp']] = {
                        'node': [],
                        'way': [],
                        'relation': []
                    }

                diff['partition'][element['@timestamp']][element_type].append(element['@id'])

            diff['osmChange'][action_type] = new

        return info | diff

    def upload_diff(self, diff: dict, comment: str, extra_tags: dict | None = None) -> str | None:
        assert 'comment' not in extra_tags
        extra_tags['comment'] = comment

        for key, value in list(extra_tags.items()):
            assert not key.startswith(TAG_PREFIX)

            if not value:
                del extra_tags[key]
                continue

            # stringify the value
            if not isinstance(value, str):
                value = str(value)
                extra_tags[key] = value

            # add revert: prefix if applicable
            if key not in NO_TAG_PREFIX:
                del extra_tags[key]
                key = f'{TAG_PREFIX}:{key}'
                extra_tags[key] = value

            # trim value if too long
            if len(value) > TAG_MAX_LENGTH:
                print(f'🚧 Warning: Trimming {key} value because it exceeds {TAG_MAX_LENGTH} characters: {value}')
                extra_tags[key] = value[:252] + '…'

        changeset = {'osm': {'changeset': {'tag': [
            {
                '@k': k,
                '@v': v
            } for k, v in extra_tags.items()
        ]}}}
        changeset_xml = xmltodict.unparse(changeset)

        r = self._http.put('/0.6/changeset/create',
                           content=changeset_xml,
                           headers=XML_HEADERS)
        r.raise_for_status()

        changeset_id = r.text
        osm_change = build_osm_change(diff, changeset_id)
        osm_change_xml = xmltodict.unparse(osm_change)

        upload_resp = self._http.post(f'/0.6/changeset/{changeset_id}/upload',
                                      content=osm_change_xml,
                                      headers=XML_HEADERS,
                                      timeout=150)

        r = self._http.put(f'/0.6/changeset/{changeset_id}/close')
        r.raise_for_status()

        if upload_resp.status_code == 409:
            print(f'🆚 Failed to upload the changes ({upload_resp.status_code})')
            print(f'🆚 {upload_resp.text}')
            print(f'🆚 The Overpass data is outdated, please try again shortly')
            return None

        if upload_resp.status_code != 200:
            print(f'😵 Failed to upload the changes ({upload_resp.status_code})')
            print(f'😵 {upload_resp.text}')
            return None

        return changeset_id

    def post_discussion_comment(self, changeset_id: int, comment: str) -> str:
        r = self._http.post(f'/0.6/changeset/{changeset_id}/comment', data={'text': comment})

        if r.is_success:
            return 'OK'

        if r.status_code in (429,):
            return 'RATE_LIMITED'

        return str(r.status_code)
