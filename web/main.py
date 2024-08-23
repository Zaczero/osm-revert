import asyncio
import os
import re
import sys
from asyncio import Semaphore, get_running_loop, timeout
from collections import defaultdict
from collections.abc import Sequence
from io import TextIOWrapper
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection
from typing import Annotated
from urllib.parse import urlencode

from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query, Request, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sentry_sdk import capture_exception, set_context, trace
from starlette.websockets import WebSocket

from config import (
    CONNECTION_LIMIT,
    OSM_CLIENT,
    OSM_SCOPES,
    OSM_SECRET,
    TEST_ENV,
    VERSION_DATE,
)
from utils import http

app = FastAPI()
app.mount('/static', StaticFiles(directory='static', html=True), name='static')

cookie_max_age = 31536000  # 1 year
templates = Jinja2Templates(directory='templates', auto_reload=TEST_ENV)
user_cache = TTLCache(maxsize=1024, ttl=7200)  # 2 hours
active_ws = defaultdict(lambda: Semaphore(CONNECTION_LIMIT))


@trace
async def fetch_user_details(request: Request) -> dict | None:
    try:
        access_token = request.cookies['access_token']
    except Exception:
        return None

    try:
        return user_cache[access_token]
    except Exception:
        async with http().get(
            'https://api.openstreetmap.org/api/0.6/user/details.json',
            headers={'Authorization': f'Bearer {access_token}'},
        ) as r:
            if not r.ok:
                return None
            user: dict = await r.json()

        if 'img' not in user:
            user['img'] = {'href': None}

        user_cache[access_token] = user
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
    authorization_url = 'https://www.openstreetmap.org/oauth2/authorize?' + urlencode(
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

    async with http().post(
        'https://www.openstreetmap.org/oauth2/token',
        data={
            'client_id': OSM_CLIENT,
            'client_secret': OSM_SECRET,
            'redirect_uri': str(request.url_for('callback')),
            'grant_type': 'authorization_code',
            'code': code,
        },
        raise_for_status=True,
    ) as r:
        access_token = (await r.json())['access_token']

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
        access_token = ws.cookies['access_token']
    except Exception:
        await ws.close(1008)
        return

    semaphore = active_ws[access_token]
    if semaphore.locked():
        await ws.close(1008, 'Too many simultaneous connections for this user')
        return

    async with semaphore:
        try:
            while True:
                args = await ws.receive_json()
                last_message = await main(ws, access_token, args)
                await ws.send_json({'message': last_message, 'last': True})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            capture_exception(e)
            await ws.close(1011, str(e))


@trace
async def main(ws: WebSocket, access_token: str, args: dict) -> str:
    for required_arg in (
        'changesets',
        'query_filter',
        'comment',
        'upload',
        'discussion',
        'discussion_target',
        'fix_parents',
    ):
        if required_arg not in args:
            raise ValueError(f'Missing argument: {required_arg!r}')

    changesets = re.split(r'(?:;|,|\s)+', args['changesets'])
    changesets = [c.strip() for c in changesets if c.strip()]
    query_filter: str = args['query_filter'].strip()
    comment: str = re.sub(r'\s{2,}', ' ', args['comment']).strip()
    upload: bool = args['upload']
    discussion: str = args['discussion']
    discussion_target: str = args['discussion_target']
    fix_parents: bool = args['fix_parents']

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

    r, w = Pipe(duplex=False)
    kwargs = {
        'conn': w,
        'env': {
            'OSM_REVERT_VERSION_DATE': VERSION_DATE,
            'OSM_REVERT_WEBSITE': os.getenv('OSM_REVERT_WEBSITE', ''),
        },
        'changeset_ids': changeset_ids,
        'comment': comment,
        'osm_token': access_token,
        'discussion': discussion,
        'discussion_target': discussion_target,
        'print_osc': print_osc,
        'query_filter': query_filter,
        'fix_parents': fix_parents,
    }
    set_context('revert', kwargs)
    loop = get_running_loop()

    @trace
    async def process_task():
        with w:
            proc = Process(target=revert_worker, kwargs=kwargs)
            proc.start()

            try:
                async with timeout(1800):  # 30 minutes
                    await loop.run_in_executor(None, proc.join)
                    return proc.exitcode
            finally:
                if proc.is_alive():
                    proc.terminate()
                    await loop.run_in_executor(None, proc.join)

    task = asyncio.create_task(process_task())

    try:
        with r, TextIOWrapper(os.fdopen(r.fileno(), 'rb', 0, closefd=False)) as reader:
            while line := await loop.run_in_executor(None, reader.readline):
                await ws.send_json({'message': line.rstrip(' \n')})
        exitcode = await task
        return f'Exit code: {exitcode}'
    finally:
        task.cancel()


def revert_worker(
    *,
    conn: Connection,
    env: dict[str, str],
    changeset_ids: Sequence[int],
    comment: str,
    osm_token: str,
    discussion: str,
    discussion_target: str,
    print_osc: bool,
    query_filter: str,
    fix_parents: bool,
) -> int:
    # redirect stdout/stderr to the pipe
    sys.stdout = sys.stderr = TextIOWrapper(os.fdopen(conn.fileno(), 'wb', 0, closefd=False), write_through=True)

    # configure environment variables
    for k, v in env.items():
        os.environ[k] = v

    import osm_revert

    return osm_revert.main(
        changeset_ids=changeset_ids,
        comment=comment,
        osm_token=osm_token,
        discussion=discussion,
        discussion_target=discussion_target,
        print_osc=print_osc,
        query_filter=query_filter,
        fix_parents=fix_parents,
    )
