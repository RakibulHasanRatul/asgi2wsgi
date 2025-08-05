import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Callable, Iterable
from typing import Any

from starlette.types import ASGIApp, Message, Scope

# Thread-local storage for event loops
_thread_local = threading.local()

#  encoding used for conversion between bytes and strings
ENCODING="latin-1"

# Type aliases
type StartResponse = Callable[[str, list[tuple[str, str]]], None]
type WSGIEnviron = dict[str, Any]
type StartQueue = queue.SimpleQueue[tuple[str, list[tuple[str, str]]]]
type ChunkQueue = queue.SimpleQueue[bytes | None]


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
        # Prepare headers from environment (keep as bytes for ASGI)
        headers: list[tuple[bytes, bytes]] = []
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

        # Construct scope with explicit types
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

        # Read request body
        request_body = b""
        if content_length := environ.get("CONTENT_LENGTH", ""):
            try:
                request_body = environ["wsgi.input"].read(int(content_length))
            except (ValueError, TypeError):
                pass

        # Create communication queues
        start_queue: StartQueue = queue.SimpleQueue()
        chunk_queue: ChunkQueue = queue.SimpleQueue()

        # Submit ASGI processing to thread pool
        self.executor.submit(
            self._run_asgi_in_thread,
            scope,
            request_body,
            start_queue,
            chunk_queue,
        ).result()

        # Get HTTP response start and convert headers to WSGI format
        status, wsgi_headers = start_queue.get()

        # Stream response body chunks
        def response_stream() -> Iterable[bytes]:
            while True:
                if (chunk := chunk_queue.get()) is None:
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
        # Get or create thread-local event loop
        if not hasattr(_thread_local, "loop"):
            _thread_local.loop = asyncio.new_event_loop()
        loop = _thread_local.loop

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = str(message["status"])
                # Convert ASGI bytes headers to WSGI string headers
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

        # Execute ASGI app with error handling
        try:
            loop.run_until_complete(self.app(scope, receive, send))
        except Exception as e:
            if start_queue.empty():
                # Create error response headers (string format)
                error_headers: list[tuple[str, str]] = [
                    ("Content-Type", "text/plain")
                ]
                start_queue.put(("500 Internal Server Error", error_headers))
                chunk_queue.put(f"ASGI Error: {e}".encode())
            chunk_queue.put(None)
            raise
