import base64
import zlib
from asyncio import Future, Task, create_task, sleep
from contextlib import asynccontextmanager
from typing import TypedDict
from uuid import uuid4
from weakref import WeakKeyDictionary

from starlette.websockets import WebSocket

from osm_revert.config import OVERPASS_RESPONSE_MAX_SIZE


class ClientError(Exception):
    pass


class _ActiveRequest(TypedDict):
    ws: WebSocket
    id: str
    url: str
    query: str
    future: Future[tuple[int, str]]
    timeout_task: Task[None]


_ACTIVE_REQUESTS: dict[str, _ActiveRequest] = {}
_WS_REQUESTS = WeakKeyDictionary[WebSocket, set[str]]()


def cleanup_request(request: str | _ActiveRequest | None) -> None:
    """Clean up a single request."""
    if isinstance(request, str):
        request = _ACTIVE_REQUESTS.get(request)
    if request is None:
        return

    if not request['future'].done():
        request['future'].cancel()
    if not request['timeout_task'].done():
        request['timeout_task'].cancel()

    _ACTIVE_REQUESTS.pop(request['id'], None)
    _WS_REQUESTS[request['ws']].discard(request['id'])


def cleanup_websocket(ws: WebSocket) -> None:
    """Clean up all requests for a disconnected WebSocket."""
    request_ids = _WS_REQUESTS.get(ws)
    if request_ids is not None:
        for request_id in request_ids.copy():
            cleanup_request(request_id)


async def _timeout_handler(request: _ActiveRequest, timeout: float) -> None:
    """Handle request timeout and cleanup."""
    await sleep(timeout)
    if not request['future'].done():
        request['future'].set_exception(TimeoutError)
    cleanup_request(request)


@asynccontextmanager
async def proxy_request(ws: WebSocket, url: str, query: str):
    request_id = str(uuid4())
    future = Future[tuple[int, str]]()

    request: _ActiveRequest = {
        'ws': ws,
        'id': request_id,
        'url': url,
        'query': query,
        'future': future,
        'timeout_task': None,  # type: ignore
    }
    request['timeout_task'] = create_task(_timeout_handler(request, timeout=180 * 2 + 60))

    _ACTIVE_REQUESTS[request_id] = request

    ws_requests = _WS_REQUESTS.get(ws)
    if ws_requests is None:
        ws_requests = _WS_REQUESTS[ws] = set()
    ws_requests.add(request_id)

    try:
        yield request_id, future
    finally:
        cleanup_request(request)


def handle_proxy_response(request_id: str, status: int, data: str | None, error: str | None) -> None:
    request = _ACTIVE_REQUESTS.get(request_id)
    if request is None or request['future'].done():
        return

    if error is not None:
        assert data is None
        request['future'].set_exception(ClientError(error))
        return

    assert data is not None
    if len(data) > OVERPASS_RESPONSE_MAX_SIZE:
        request['future'].set_exception(ValueError('Content Too Large'))
        return

    decompressor = zlib.decompressobj()
    decompressed = decompressor.decompress(base64.b64decode(data), OVERPASS_RESPONSE_MAX_SIZE)
    if decompressor.unconsumed_tail:
        request['future'].set_exception(ValueError('Content Too Large'))
        return

    data = decompressed.decode()
    request['future'].set_result((status, data))
