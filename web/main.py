import io
import os
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection

import anyio
from anyio import WouldBlock, create_task_group, fail_after, get_cancelled_exc_class, open_file, to_thread
from authlib.integrations.httpx_client import AsyncOAuth2Client
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sentry_sdk import capture_exception, trace
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocket

from config import (
    CONNECTION_LIMIT,
    INSTANCE_SECRET,
    OSM_CLIENT,
    OSM_SCOPES,
    OSM_SECRET,
    TEST_ENV,
    USER_AGENT,
    VERSION_DATE,
)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=INSTANCE_SECRET, max_age=31536000)  # 1 year
app.mount('/static', StaticFiles(directory='static', html=True), name='static')

templates = Jinja2Templates(directory='templates', auto_reload=TEST_ENV)
user_cache = TTLCache(maxsize=1024, ttl=7200)  # 2 hours
active_ws = defaultdict(lambda: anyio.Semaphore(CONNECTION_LIMIT))


@trace
async def fetch_user_details(request: Request) -> dict | None:
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
        async with AsyncOAuth2Client(token=token, headers={'User-Agent': USER_AGENT}) as http:
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
        return templates.TemplateResponse(request, 'authorized.jinja2', {'user': user})
    else:
        return templates.TemplateResponse(request, 'index.jinja2')


@app.post('/login')
async def login(request: Request):
    async with AsyncOAuth2Client(
        client_id=OSM_CLIENT,
        scope=OSM_SCOPES,
        redirect_uri=str(request.url_for('callback')),
    ) as http:
        authorization_url, state = http.create_authorization_url('https://www.openstreetmap.org/oauth2/authorize')

    request.session['oauth_state'] = state
    return RedirectResponse(authorization_url, status.HTTP_303_SEE_OTHER)


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
        headers={'User-Agent': USER_AGENT},
    ) as http:
        token = await http.fetch_token(
            'https://www.openstreetmap.org/oauth2/token',
            authorization_response=str(request.url),
        )

    request.session['oauth_token'] = token
    return RedirectResponse('/', status.HTTP_302_FOUND)


@app.post('/logout')
async def logout(request: Request):
    request.session.pop('oauth_token', None)
    return RedirectResponse('/', status.HTTP_302_FOUND)


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

    try:
        semaphore.acquire_nowait()
    except WouldBlock:
        await ws.close(1008, 'Too many simultaneous connections for this user')
        return

    try:
        while True:
            args = await ws.receive_json()
            last_message = await main(ws, args)
            await ws.send_json({'message': last_message, 'last': True})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        capture_exception(e)
        await ws.close(1011, str(e))
    finally:
        semaphore.release()


@trace
async def main(ws: WebSocket, args: dict) -> str:
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
    oauth_token = ws.session['oauth_token']
    print_osc = not upload

    r, w = Pipe(duplex=False)
    exitcode = None

    with fail_after(300), r, w:  # 5 minutes

        @trace
        async def process_task():
            nonlocal exitcode

            proc = Process(
                target=revert_worker,
                kwargs={
                    'conn': w,
                    'env': {
                        'OSM_REVERT_VERSION_DATE': VERSION_DATE,
                        'OSM_REVERT_WEBSITE': os.getenv('OSM_REVERT_WEBSITE', ''),
                    },
                    'changeset_ids': changeset_ids,
                    'comment': comment,
                    'oauth_token': oauth_token,
                    'discussion': discussion,
                    'discussion_target': discussion_target,
                    'print_osc': print_osc,
                    'query_filter': query_filter,
                    'fix_parents': fix_parents,
                },
            )

            proc.start()

            try:
                await to_thread.run_sync(proc.join, cancellable=True)
            except get_cancelled_exc_class():
                proc.kill()
                raise

            w.send_bytes(b'EOF\n')
            exitcode = proc.exitcode

        async with create_task_group() as tg:
            tg.start_soon(process_task)

            async with await open_file(r.fileno(), closefd=False) as stdout:
                async for line in stdout:
                    if line.endswith('EOF\n'):
                        break
                    await ws.send_json({'message': line.rstrip(' \n')})

    return f'Exit code: {exitcode}'


def revert_worker(
    *,
    conn: Connection,
    env: dict[str, str],
    changeset_ids: Sequence[int],
    comment: str,
    oauth_token: dict,
    discussion: str,
    discussion_target: str,
    print_osc: bool,
    query_filter: str,
    fix_parents: bool,
) -> int:
    # Redirect stdout to the pipe
    sys.stdout = io.TextIOWrapper(os.fdopen(conn.fileno(), 'wb', buffering=0), write_through=True)

    for k, v in env.items():
        os.environ[k] = v

    import osm_revert

    return osm_revert.main(
        changeset_ids=changeset_ids,
        comment=comment,
        oauth_token=oauth_token,
        discussion=discussion,
        discussion_target=discussion_target,
        print_osc=print_osc,
        query_filter=query_filter,
        fix_parents=fix_parents,
    )
