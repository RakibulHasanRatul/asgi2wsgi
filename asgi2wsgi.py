import asyncio
import concurrent.futures
import queue
import sys
import threading
import traceback
from collections.abc import Callable, Iterable
from typing import Any, Awaitable, MutableMapping

# Thread-local storage for event loops
_thread_local = threading.local()

# Encoding used for headers
ENCODING = "latin-1"
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB cap for body read

# Type aliases (Python 3.12+ syntax)
type StartResponse = Callable[[str, list[tuple[str, str]]], None]
type WSGIEnviron = dict[str, Any]
type StartQueue = queue.SimpleQueue[tuple[str, list[tuple[str, str]]]]
type ChunkQueue = queue.SimpleQueue[bytes | None]
type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Send = Callable[[Message], Awaitable[None]]
type Receive = Callable[[], Awaitable[Message]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class ASGI2WSGI:
    def __init__(self, app: ASGIApp, num_workers: int = 4) -> None:
        self.app = app
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=num_workers
        )

    def __call__(
        self,
        environ: WSGIEnviron,
        start_response: StartResponse,
    ) -> Iterable[bytes]:
        headers: list[tuple[bytes, bytes]] = []

        # Extract request headers
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name_bytes = key[5:].replace("_", "-").lower().encode(ENCODING)
                headers.append((name_bytes, value.encode(ENCODING)))

        # Add content headers if present
        for header_name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if value := environ.get(header_name):
                name_bytes = (
                    header_name.replace("_", "-").lower().encode(ENCODING)
                )
                headers.append((name_bytes, value.encode(ENCODING)))

        # Construct ASGI scope
        server_port = int(environ["SERVER_PORT"])
        remote_port = int(environ.get("REMOTE_PORT", "0"))
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split(
                "/"
            )[1],
            "method": environ["REQUEST_METHOD"],
            "headers": headers,
            "path": environ.get("PATH_INFO", "/"),
            "root_path": environ.get("SCRIPT_NAME", ""),
            "raw_path": environ["PATH_INFO"].encode(),
            "query_string": environ.get("QUERY_STRING", "").encode(),
            "server": (environ["SERVER_NAME"], server_port),
            "client": (environ.get("REMOTE_ADDR", "127.0.0.1"), remote_port),
            "scheme": environ.get("wsgi.url_scheme", "http"),
            "extensions": {},
        }

        # Read request body safely
        request_body = b""
        if content_length := environ.get("CONTENT_LENGTH", ""):
            try:
                length = min(int(content_length), MAX_BODY_SIZE)
                request_body = environ["wsgi.input"].read(length)
            except (ValueError, TypeError):
                pass

        # Queues for communication
        start_queue: StartQueue = queue.SimpleQueue()
        chunk_queue: ChunkQueue = queue.SimpleQueue()

        # Submit ASGI handler to thread pool and block until headers are ready
        future = self.executor.submit(
            self._run_asgi_in_thread,
            scope,
            request_body,
            start_queue,
            chunk_queue,
        )
        future.result()  # Blocking here is okay in WSGI

        status, wsgi_headers = start_queue.get()

        # Generator to yield response chunks
        def response_stream() -> Iterable[bytes]:
            while True:
                chunk = chunk_queue.get()
                if chunk is None:
                    break
                yield chunk

        start_response(status, wsgi_headers)
        return response_stream()

    def _run_asgi_in_thread(
        self,
        scope: Scope,
        request_body: bytes,
        start_queue: StartQueue,
        chunk_queue: ChunkQueue,
    ) -> None:
        # Ensure a new event loop for each thread (or reuse safely)
        if not hasattr(_thread_local, "loop") or _thread_local.loop.is_closed():
            _thread_local.loop = asyncio.new_event_loop()
        loop = _thread_local.loop

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = str(message["status"])
                wsgi_headers: list[tuple[str, str]] = [
                    (k.decode(ENCODING), v.decode(ENCODING))
                    for k, v in message["headers"]
                ]
                start_queue.put((status, wsgi_headers))
            elif message["type"] == "http.response.body":
                if body := message.get("body", b""):
                    chunk_queue.put(body)
                if not message.get("more_body", False):
                    chunk_queue.put(None)

        async def receive() -> Message:
            return {
                "type": "http.request",
                "body": request_body,
                "more_body": False,
            }

        try:
            loop.run_until_complete(self.app(scope, receive, send))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            if start_queue.empty():
                start_queue.put(
                    (
                        "500 Internal Server Error",
                        [("Content-Type", "text/plain")],
                    )
                )
                chunk_queue.put(f"ASGI Error: {e}".encode())
            chunk_queue.put(None)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
