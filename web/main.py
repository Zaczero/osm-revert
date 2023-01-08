import os
import re
from typing import Optional

import asyncio
from authlib.integrations.starlette_client import OAuth
from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import Serializer
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

oauth = OAuth()
oauth.register(
    name='osm',
    client_id=os.getenv('CONSUMER_KEY'),
    client_secret=os.getenv('CONSUMER_SECRET'),
    request_token_url='https://www.openstreetmap.org/oauth/request_token',
    access_token_url='https://www.openstreetmap.org/oauth/access_token',
    authorize_url='https://www.openstreetmap.org/oauth/authorize'
)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv('INSTANCE_SECRET'))
app.mount('/static', StaticFiles(directory='static', html=True), name='static')

secret = Serializer(os.getenv('INSTANCE_SECRET'))
templates = Jinja2Templates(directory='templates')

user_cache = TTLCache(maxsize=1024, ttl=3600)  # 1 hour cache
active_ws = {}


async def fetch_user_details(request: Request) -> Optional[dict]:
    if 'token' not in request.cookies:
        return None

    try:
        token = secret.loads(request.cookies['token'])
    except Exception:
        return None

    user_cache_key = token['oauth_token_secret']

    try:
        return user_cache[user_cache_key]
    except Exception:
        response = await oauth.osm.get('https://api.openstreetmap.org/api/0.6/user/details.json', token=token)

        if response.status_code != 200:
            return None

        try:
            user = response.json()['user']
        except Exception:
            return None

        user_cache[user_cache_key] = user
        return user


@app.get('/')
@app.post('/')
async def index(request: Request):
    if user := await fetch_user_details(request):
        return templates.TemplateResponse('authorized.html', {'request': request, 'user': user})
    else:
        return templates.TemplateResponse('index.html', {'request': request})


@app.post('/login')
async def login(request: Request):
    return await oauth.osm.authorize_redirect(request, request.url_for('callback'))


@app.get('/callback')
async def callback(request: Request):
    token = await oauth.osm.authorize_access_token(request)

    response = RedirectResponse(request.url_for('index'))
    response.set_cookie('token', secret.dumps(token),
                        max_age=(3600 * 24 * 365),
                        secure=request.url.scheme == 'https',
                        httponly=True)

    return response


@app.post('/logout')
async def logout(request: Request):
    response = RedirectResponse(request.url_for('index'))
    response.set_cookie('token', '', max_age=0)

    return response


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    if 'token' not in ws.cookies:
        await ws.close(401)
        return

    try:
        session_id = secret.loads(ws.cookies['token'])['oauth_token_secret']
    except Exception:
        await ws.close(401)
        return

    if session_id in active_ws:
        await ws.close(403, 'Only one WebSocket connection is allowed per user')
        return

    await ws.accept()

    active_ws[session_id] = ws

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
        active_ws.pop(session_id, None)


async def main(ws: WebSocket, args: dict) -> str:
    assert 'changesets' in args, 'Bad request'
    assert 'comment' in args, 'Bad request'

    changesets = re.split(r'(;|,|\s)+', args['changesets'])
    changesets = [c.strip() for c in changesets if c.strip()]
    comment = re.sub(r'\s{2,}', ' ', args['comment']).strip()

    if not changesets:
        return '❗️ No changesets were provided'

    if len(changesets) > 10:
        return '❗️ For safety, you can only revert up to 10 changesets at a time'

    if not all(c.isnumeric() for c in changesets):
        return '❗️ One or more changesets contain non-numeric characters'

    if not comment:
        return '❗️ No comment was provided for the changes'

    token = secret.loads(ws.cookies['token'])
    version_suffix = os.getenv('OSM_REVERT_VERSION_SUFFIX', '')
    consumer_key = os.getenv('CONSUMER_KEY')
    consumer_secret = os.getenv('CONSUMER_SECRET')

    process = await asyncio.create_subprocess_exec(
        'docker', 'run', '--rm',
        '--env', f'OSM_REVERT_VERSION_SUFFIX={version_suffix}',
        '--env', f'CONSUMER_KEY={consumer_key}',
        '--env', f'CONSUMER_SECRET={consumer_secret}',
        'zaczero/osm-revert',
        '--changeset_ids', ','.join(changesets),
        '--comment', comment,
        '--oauth_token', token['oauth_token'],
        '--oauth_token_secret', token['oauth_token_secret'],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)

    try:
        while True:
            line = await process.stdout.readline()

            if not line:
                break

            await ws.send_json({'message': line.decode('utf-8').strip()})
    finally:
        if process.returncode is None:
            process.kill()

    return f'Exit code: {process.returncode}'
