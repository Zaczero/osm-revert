import asyncio
import json
import os
import re
from collections import defaultdict
from shlex import quote
from typing import Optional

from authlib.integrations.httpx_client import AsyncOAuth2Client
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

from config import (CONNECTION_LIMIT, INSTANCE_SECRET, OSM_CLIENT, OSM_SCOPES,
                    OSM_SECRET, USER_AGENT)

INDEX_REDIRECT = RedirectResponse('/', status_code=status.HTTP_302_FOUND)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=INSTANCE_SECRET, max_age=31536000)  # 1 year
app.mount('/static', StaticFiles(directory='static', html=True), name='static')

templates = Jinja2Templates(directory='templates')

user_cache = TTLCache(maxsize=1024, ttl=3600)  # 1 hour cache
active_ws = defaultdict(lambda: asyncio.Semaphore(CONNECTION_LIMIT))


async def fetch_user_details(request: Request) -> Optional[dict]:
    if 'oauth_token' not in request.session:
        return None

    try:
        token = request.session['oauth_token']
    except Exception:
        return None

    user_cache_key = token['access_token']

    try:
        return user_cache[user_cache_key]
    except Exception:
        async with AsyncOAuth2Client(
                token=token,
                headers={'User-Agent': USER_AGENT}) as http:
            response = await http.get('https://api.openstreetmap.org/api/0.6/user/details.json')

        if response.status_code != 200:
            return None

        try:
            user = response.json()['user']
        except Exception:
            return None

        if 'img' not in user:
            user['img'] = {'href': None}

        user_cache[user_cache_key] = user
        return user


@app.get('/')
@app.post('/')
async def index(request: Request):
    if user := await fetch_user_details(request):
        return templates.TemplateResponse('authorized.jinja2', {'request': request, 'user': user})
    else:
        return templates.TemplateResponse('index.jinja2', {'request': request})


@app.post('/login')
async def login(request: Request):
    async with AsyncOAuth2Client(
            client_id=OSM_CLIENT,
            scope=OSM_SCOPES,
            redirect_uri=str(request.url_for('callback'))) as http:
        authorization_url, state = http.create_authorization_url('https://www.openstreetmap.org/oauth2/authorize')

    request.session['oauth_state'] = state
    return RedirectResponse(authorization_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get('/callback')
async def callback(request: Request):
    state = request.session.pop('oauth_state', None)

    if state is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Invalid OAuth state')

    async with AsyncOAuth2Client(
            client_id=OSM_CLIENT,
            client_secret=OSM_SECRET,
            redirect_uri=str(request.url_for('callback')),
            state=state,
            headers={'User-Agent': USER_AGENT}) as http:
        token = await http.fetch_token('https://www.openstreetmap.org/oauth2/token', authorization_response=str(request.url))

    request.session['oauth_token'] = token
    return INDEX_REDIRECT


@app.post('/logout')
async def logout(request: Request):
    request.session.pop('oauth_token', None)
    return INDEX_REDIRECT


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    await ws.accept()

    if 'oauth_token' not in ws.session:
        await ws.close(1008)
        return

    try:
        session_id = ws.session['oauth_token']['access_token']
    except Exception:
        await ws.close(1008)
        return

    semaphore = active_ws[session_id]

    if semaphore.locked():
        await ws.close(1008, 'Too many simultaneous connections for this user')
        return

    await semaphore.acquire()

    try:
        while True:
            args = await ws.receive_json()
            last_message = await main(ws, args)
            await ws.send_json({'message': last_message, 'last': True})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await ws.close(1011, str(e))
    finally:
        semaphore.release()


async def main(ws: WebSocket, args: dict) -> str:
    for required_arg in ('changesets', 'query_filter', 'comment', 'upload', 'discussion', 'discussion_target'):
        assert required_arg in args, f'Missing argument: {required_arg}'

    changesets = re.split(r'(?:;|,|\s)+', args['changesets'])
    changesets = [c.strip() for c in changesets if c.strip()]
    query_filter = args['query_filter'].strip()
    comment = re.sub(r'\s{2,}', ' ', args['comment']).strip()
    upload = args['upload']
    discussion = args['discussion']
    discussion_target = args['discussion_target']

    if not changesets:
        return '❗️ No changesets were provided'

    if not all(c.isnumeric() for c in changesets):
        return '❗️ One or more changesets contain non-numeric characters'

    # upload specific requirements
    if upload:
        if not comment:
            return '❗️ No comment was provided for the changes'

    assert discussion_target in {'all', 'newest', 'oldest'}, 'Invalid discussion target'

    token = ws.session['oauth_token']
    version_suffix = os.getenv('OSM_REVERT_VERSION_SUFFIX', '')
    website = os.getenv('OSM_REVERT_WEBSITE', '')

    if upload:
        extra_args = []
    else:
        extra_args = ['--print_osc', 'True']

    process = await asyncio.create_subprocess_exec(
        'docker', 'run', '--rm',
        '--env', f'OSM_REVERT_VERSION_SUFFIX={version_suffix}',
        '--env', f'OSM_REVERT_WEBSITE={website}',
        'zaczero/osm-revert',
        '--changeset_ids', quote(','.join(changesets)),
        '--query_filter', quote(query_filter),
        '--comment', quote(comment),
        '--oauth_token', quote(json.dumps(token)),
        '--discussion', quote(discussion),
        '--discussion_target', quote(discussion_target),
        *extra_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)

    try:
        while True:
            line = await process.stdout.readline()

            if not line:
                break

            await ws.send_json({'message': line.decode('utf-8').rstrip()})
    finally:
        if process.returncode is None:
            process.kill()

    return f'Exit code: {process.returncode}'
