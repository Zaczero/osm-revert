import os
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import Serializer
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocket

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
is_https = os.getenv('HTTPS', '0') == '1'

user_cache = TTLCache(maxsize=1024, ttl=3600)  # 1 hour cache


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
                        secure=is_https,
                        httponly=True,
                        samesite='strict' if is_https else 'lax')

    return response


@app.post('/logout')
async def logout(request: Request):
    response = RedirectResponse(request.url_for('index'))
    response.set_cookie('token', '', max_age=0)

    return response


@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    while True:
        data = await ws.receive_text()
        await ws.send_text(f'Message text was: {data}')
