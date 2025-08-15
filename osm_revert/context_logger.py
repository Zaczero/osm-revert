from asyncio import Queue
from contextlib import contextmanager
from contextvars import ContextVar

_log_queue: ContextVar[Queue[str]] = ContextVar('log_queue')


@contextmanager
def context_logger():
    queue: Queue[str] = Queue()
    token = _log_queue.set(queue)
    try:
        yield queue
    finally:
        _log_queue.reset(token)
        queue.shutdown()


def context_print(msg: str) -> None:
    queue = _log_queue.get()
    if queue is not None:
        queue.put_nowait(msg)
    else:
        print(msg)
