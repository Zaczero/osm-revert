from collections.abc import Collection
from typing import Any

import xmltodict
from pydantic import SecretStr
from sentry_sdk import trace

from osm_revert.config import CREATED_BY, NO_TAG_PREFIX, OSM_API_URL, TAG_MAX_LENGTH, TAG_PREFIX
from osm_revert.context_logger import context_print
from osm_revert.utils import ensure_iterable, get_http_client, retry_exponential


@trace
def sort_relations_for_osm_change(relations: Collection[dict]) -> list[dict]:
    change_ids = {rel['@id'] for rel in relations}

    # tuples: (relation, set of relation ids it depends on)
    dependency_state = {
        rel['@id']: (
            rel,
            change_ids.intersection(
                m['@ref']  #
                for m in ensure_iterable(rel.get('member', ()))
                if m['@type'] == 'relation'
            ),
        )
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
        context_print(f'🚧 Warning: relation/{rel["@id"]} has {len(deps)} circular dependencies')
        result.append(rel)

    return result


@trace
def build_osm_change(diff: dict, changeset_id: str | None) -> dict:
    result = {
        'osmChange': {
            '@version': 0.6,
            '@generator': CREATED_BY,
            'modify': {'node': [], 'way': [], 'relation': []},
            'delete': {'relation': [], 'way': [], 'node': []},
        }
    }

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
    def __init__(self, access_token: SecretStr):
        self._http = get_http_client(
            f'{OSM_API_URL}/api',
            headers={'Authorization': f'Bearer {access_token.get_secret_value()}'},
        )

    @retry_exponential
    async def get_changeset_max_size(self) -> int:
        r = await self._http.get('/capabilities')
        r.raise_for_status()
        caps = xmltodict.parse(r.text)
        return int(caps['osm']['api']['changesets']['@maximum_elements'])

    @retry_exponential
    async def get_authorized_user(self) -> dict:
        r = await self._http.get('/0.6/user/details.json')
        r.raise_for_status()
        return r.json()['user']

    @retry_exponential
    async def get_user(self, uid: str | int) -> dict | None:
        r = await self._http.get(f'/0.6/user/{uid}.json')
        # allow for not found users
        if r.status_code in (404, 410):
            return None
        r.raise_for_status()
        return r.json()['user']

    @retry_exponential
    @trace
    async def get_changeset(self, changeset_id: int) -> dict:
        info_resp = await self._http.get(f'/0.6/changeset/{changeset_id}')
        info_resp.raise_for_status()

        diff_resp = await self._http.get(f'/0.6/changeset/{changeset_id}/download')
        diff_resp.raise_for_status()

        info = xmltodict.parse(info_resp.text)
        diff = xmltodict.parse(diff_resp.text)
        diff['partition'] = {}

        for action_type, affected_elements in diff['osmChange'].items():
            if action_type.startswith('@'):
                continue

            new = {'node': [], 'way': [], 'relation': []}

            for affected_element in ensure_iterable(affected_elements):
                element_type, element = next(iter(affected_element.items()))

                new[element_type].append(element)

                if element['@timestamp'] not in diff['partition']:
                    diff['partition'][element['@timestamp']] = {'node': [], 'way': [], 'relation': []}

                diff['partition'][element['@timestamp']][element_type].append(element['@id'])

            diff['osmChange'][action_type] = new

        return info | diff

    @trace
    async def upload_diff(self, diff: dict, comment: str, extra_tags: dict[str, Any]) -> str | None:
        if 'comment' in extra_tags:
            raise ValueError('comment is a reserved tag')

        extra_tags['comment'] = comment

        for key, value in tuple(extra_tags.items()):
            if key.startswith(TAG_PREFIX):
                raise ValueError(f'{key!r} is a reserved tag')

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
                context_print(
                    f'🚧 Warning: Trimming {key} value because it exceeds {TAG_MAX_LENGTH} characters: {value}'
                )
                extra_tags[key] = value[:252] + '…'

        changeset = {'osm': {'changeset': {'tag': [{'@k': k, '@v': v} for k, v in extra_tags.items()]}}}
        changeset_xml = xmltodict.unparse(changeset)

        r = await self._http.put(
            '/0.6/changeset/create',
            content=changeset_xml,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
        )
        r.raise_for_status()

        changeset_id = r.text
        osm_change = build_osm_change(diff, changeset_id)
        osm_change_xml = xmltodict.unparse(osm_change)

        upload_resp = await self._http.post(
            f'/0.6/changeset/{changeset_id}/upload',
            content=osm_change_xml,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            timeout=150,
        )

        r = await self._http.put(f'/0.6/changeset/{changeset_id}/close')
        r.raise_for_status()

        if upload_resp.status_code == 409:
            context_print(f'🆚 Failed to upload the changes ({upload_resp.status_code})')
            context_print(f'🆚 {upload_resp.text}')
            context_print('🆚 The Overpass data is outdated, please try again shortly')
            return None

        if upload_resp.status_code != 200:
            context_print(f'😵 Failed to upload the changes ({upload_resp.status_code})')
            context_print(f'😵 {upload_resp.text}')
            return None

        return changeset_id

    async def post_discussion_comment(self, changeset_id: int, comment: str) -> str:
        r = await self._http.post(f'/0.6/changeset/{changeset_id}/comment', data={'text': comment})
        if r.is_success:
            return 'OK'
        if r.status_code in (429,):
            return 'RATE_LIMITED'
        return str(r.status_code)
