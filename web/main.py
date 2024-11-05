import os
import re
from asyncio import Queue, QueueShutDown, Semaphore, TaskGroup, timeout
from collections import defaultdict
from contextlib import suppress
from typing import Annotated
from urllib.parse import urlencode

from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query, Request, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import AsyncClient
from pydantic import BaseModel, SecretStr
from sentry_sdk import capture_exception, start_span, trace
from starlette.websockets import WebSocket

from osm_revert.config import (
    CONNECTION_LIMIT,
    OSM_API_URL,
    OSM_CLIENT,
    OSM_SCOPES,
    OSM_SECRET,
    OSM_URL,
    TEST_ENV,
    USER_AGENT,
)
from osm_revert.context_logger import context_logger
from osm_revert.main import main as revert_main

if not OSM_CLIENT or not OSM_SECRET:
    raise AssertionError('Web interface requires OSM_CLIENT and OSM_SECRET to be set')

_RE_CHANGESET_SEPARATOR = re.compile(r'(?:;|,|\s)+')
_RE_REPEATED_WHITESPACE = re.compile(r'\s{2,}')

http = AsyncClient(
    headers={'User-Agent': USER_AGENT},
    timeout=15,
    follow_redirects=True,
)

app = FastAPI()
app.mount('/static', StaticFiles(directory='web/static', html=True), name='static')

cookie_max_age = 31536000  # 1 year
templates = Jinja2Templates(directory='web/templates', auto_reload=TEST_ENV)
user_cache = TTLCache(maxsize=1024, ttl=7200)  # 2 hours
active_ws = defaultdict(lambda: Semaphore(CONNECTION_LIMIT))


@trace
async def fetch_user_details(request: Request) -> dict | None:
    try:
        access_token = SecretStr(request.cookies['access_token'])
    except Exception:
        return None

    try:
        return user_cache[access_token.get_secret_value()]
    except Exception:
        r = await http.get(
            f'{OSM_API_URL}/api/0.6/user/details.json',
            headers={'Authorization': f'Bearer {access_token.get_secret_value()}'},
        )
        if not r.is_success:
            return None
        user = r.json()

        if 'img' not in user:
            user['img'] = {'href': None}

        user_cache[access_token.get_secret_value()] = user
        return user


@app.get('/')
@app.post('/')
async def index(request: Request):
    if user := await fetch_user_details(request):
        return templates.TemplateResponse(request, 'authorized.jinja2', {'user': user})
    else:
        return templates.TemplateResponse(request, 'index.jinja2')


@app.post('/login')
async def login(request: Request):
    state = os.urandom(32).hex()
    authorization_url = f'{OSM_URL}/oauth2/authorize?' + urlencode(
        {
            'client_id': OSM_CLIENT,
            'redirect_uri': str(request.url_for('callback')),
            'response_type': 'code',
            'scope': OSM_SCOPES,
            'state': state,
        }
    )
    response = RedirectResponse(authorization_url, status.HTTP_303_SEE_OTHER)
    response.set_cookie('oauth_state', state, secure=not TEST_ENV, httponly=True)
    return response


@app.get('/callback')
async def callback(request: Request, code: Annotated[str, Query()], state: Annotated[str, Query()]):
    cookie_state = request.cookies.get('oauth_state')
    if cookie_state != state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Invalid OAuth state')

    r = await http.post(
        f'{OSM_URL}/oauth2/token',
        data={
            'client_id': OSM_CLIENT,
            'client_secret': OSM_SECRET,
            'redirect_uri': str(request.url_for('callback')),
            'grant_type': 'authorization_code',
            'code': code,
        },
    )
    r.raise_for_status()
    access_token = r.json()['access_token']

    response = RedirectResponse('/', status.HTTP_302_FOUND)
    response.set_cookie('access_token', access_token, cookie_max_age, secure=not TEST_ENV, httponly=True)
    return response


@app.post('/logout')
async def logout():
    response = RedirectResponse('/', status.HTTP_302_FOUND)
    response.delete_cookie('access_token')
    return response


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    await ws.accept()

    try:
        access_token = SecretStr(ws.cookies['access_token'])
    except Exception:
        await ws.close(1008)
        return

    semaphore = active_ws[access_token.get_secret_value()]
    if semaphore.locked():
        await ws.close(1008, 'Too many simultaneous connections for this user')
        return

    async with semaphore:
        try:
            while True:
                args = MainArgs(**(await ws.receive_json()))
                last_message = await main(ws, access_token, args)
                await ws.send_json({'message': last_message, 'last': True})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            capture_exception(e)
            await ws.close(1011, str(e))


class MainArgs(BaseModel):
    changesets: str
    query_filter: str
    comment: str
    upload: bool
    discussion: str
    discussion_target: str
    fix_parents: bool


@trace
async def main(ws: WebSocket, access_token: SecretStr, args: MainArgs) -> str:
    changesets = _RE_CHANGESET_SEPARATOR.split(args.changesets)
    changesets = tuple(c.strip() for c in changesets if c.strip())
    query_filter = args.query_filter.strip()
    comment = _RE_REPEATED_WHITESPACE.sub(' ', args.comment).strip()
    upload = args.upload
    discussion = args.discussion.strip()
    discussion_target = args.discussion_target
    fix_parents = args.fix_parents

    if not changesets:
        return '❗️ No changesets were provided'
    if not all(c.isnumeric() for c in changesets):
        return '❗️ One or more changesets contain non-numeric characters'
    # upload specific requirements
    if upload and not comment:
        return '❗️ No comment was provided for the changes'
    if discussion_target not in ('all', 'newest', 'oldest'):
        return '❗️ Invalid discussion target'

    changeset_ids = tuple(map(int, changesets))
    print_osc = not upload

    async def queue_processor(queue: Queue):
        with suppress(QueueShutDown):
            while True:
                await ws.send_json({'message': await queue.get()})

    async with TaskGroup() as tg:
        with context_logger() as queue, start_span(op='revert', name='revert'):
            tg.create_task(queue_processor(queue))
            async with timeout(1800):  # 30 minutes
                exit_code = await revert_main(
                    changeset_ids=changeset_ids,
                    comment=comment,
                    access_token=access_token,
                    discussion=discussion,
                    discussion_target=discussion_target,
                    print_osc=print_osc,
                    query_filter=query_filter,
                    fix_parents=fix_parents,
                )
    return f'Exit code: {exit_code}'
