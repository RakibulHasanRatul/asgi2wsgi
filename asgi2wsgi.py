import asyncio
import concurrent.futures
import queue
import sys
import threading
import traceback
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Iterable, MutableMapping

# Thread-local storage to manage asyncio event loops, ensuring each thread
# gets its own independent loop for running ASGI applications.
_thread_local = threading.local()

# Default encoding for HTTP headers (typically Latin-1).
ENCODING = "latin-1"
# Maximum size for the request body to be read into memory (10 MB).
# This prevents excessive memory consumption from large uploads.
MAX_BODY_SIZE = 10 * 1024 * 1024

# Type aliases for enhanced readability and type safety (Python 3.12+ syntax).
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
    """
    ASGI2WSGI is an adapter class designed to enable ASGI (Asynchronous Server Gateway Interface)
    applications, such as FastAPI or Starlette, to run within traditional WSGI (Web Server
    Gateway Interface) environments like Gunicorn, Apache with mod_wsgi, or cPanel's Passenger.

    This adapter works by wrapping an ASGI application, translating incoming WSGI requests
    into the ASGI "scope" and "receive" callable, and then transforming the ASGI "send"
    messages back into WSGI responses. The ASGI application's asynchronous nature is
    managed by executing it within a dedicated thread pool, each thread having its
    own asyncio event loop.

    Key Features:
    - Broad ASGI Framework Compatibility: Designed to work seamlessly with any ASGI 3.0
      compliant application, including popular frameworks like FastAPI and Starlette.
    - Performance Optimized: Engineered for minimal overhead, translating between protocols
      efficiently to maintain application performance.
    - Robust Type Safety: Implemented with strict type annotations, leveraging Python 3.12+
      syntax for clarity and maintainability.
    - Easy Integration: Provides a straightforward way to deploy ASGI applications
      in existing WSGI server setups.

    Usage Example:
    ```python
    from fastapi import FastAPI
    # from asgi2wsgi import ASGI2WSGI # Assuming asgi2wsgi.py is accessible

    # Your ASGI application instance
    my_asgi_app = FastAPI()

    @my_asgi_app.get("/")
    async def read_root():
        return {"message": "Hello from ASGI!"}

    # Wrap your ASGI application to create a WSGI callable
    application = ASGI2WSGI(my_asgi_app)

    # The 'application' object can now be served by any WSGI server
    # (e.g., Gunicorn, Apache with mod_wsgi, cPanel's Passenger).
    ```

    Example for cPanel (typically in a `passenger_wsgi.py` file):
    ```python
    import os
    import sys

    # Add your application's root directory to the Python path
    sys.path.insert(0, os.path.dirname(__file__))

    from fastapi import FastAPI
    # from asgi2wsgi import ASGI2WSGI # Ensure this file is in your path

    # Initialize your ASGI application
    app = FastAPI()

    @app.get("/")
    async def read_root():
        return {"message": "Hello from ASGI on cPanel!"}

    # Create the WSGI application instance for Passenger
    application = ASGI2WSGI(app)
    ```
    """

    def __init__(self, app: ASGIApp, num_workers: int = 1) -> None:
        """
        Initializes the ASGI2WSGI adapter with the target ASGI application.

        Args:
            app: The ASGI application callable (e.g., an instance of FastAPI, Starlette).
                 It must adhere to the ASGI 3.0 specification.
            num_workers: The number of threads in the internal thread pool. This pool
                         is used to execute the asynchronous ASGI application for
                         each concurrent WSGI request. A higher number increases
                         concurrency but also resource usage. Defaults to 1.
        """
        self.app = app
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=num_workers
        )

    def __call__(
        self,
        environ: WSGIEnviron,
        start_response: StartResponse,
    ) -> Iterable[bytes]:
        """
        This method makes the ASGI2WSGI instance a WSGI callable.
        It serves as the main entry point for WSGI servers, translating the WSGI
        request environment into an ASGI "scope" and "receive" mechanism,
        and then running the ASGI application to produce a WSGI response.

        Args:
            environ: A dictionary containing the WSGI environment variables,
                     representing the incoming HTTP request.
            start_response: A WSGI callable used to send the HTTP status line
                            and response headers back to the client.

        Returns:
            An iterable of bytes, which constitutes the response body.
            The body is streamed asynchronously as chunks from the ASGI application.
        """
        request_headers: list[tuple[bytes, bytes]] = []

        # Extract HTTP headers from the WSGI environment.
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                # Convert 'HTTP_ACCEPT_ENCODING' to 'accept-encoding'
                header_name = key[5:].replace("_", "-").lower().encode(ENCODING)
                request_headers.append((header_name, value.encode(ENCODING)))

        # Add standard content headers if present in the environment.
        for header_name_key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if value := environ.get(header_name_key):
                header_name = (
                    header_name_key.replace("_", "-").lower().encode(ENCODING)
                )
                request_headers.append((header_name, value.encode(ENCODING)))

        # Construct the ASGI scope dictionary from the WSGI environment.
        server_port = int(environ["SERVER_PORT"])
        remote_port = int(environ.get("REMOTE_PORT", "0"))
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split(
                "/"
            )[1],
            "method": environ["REQUEST_METHOD"],
            "headers": request_headers,
            "path": environ.get("PATH_INFO", "/"),
            "root_path": environ.get("SCRIPT_NAME", ""),
            "raw_path": environ["PATH_INFO"].encode(ENCODING),
            "query_string": environ.get("QUERY_STRING", "").encode(ENCODING),
            "server": (environ["SERVER_NAME"], server_port),
            "client": (environ.get("REMOTE_ADDR", "127.0.0.1"), remote_port),
            "scheme": environ.get("wsgi.url_scheme", "http"),
            "extensions": {},  # ASGI extensions can be added here if supported
        }

        # Read the request body. WSGI servers usually provide it via wsgi.input.
        request_body = b""
        if content_length_str := environ.get("CONTENT_LENGTH", ""):
            try:
                length = min(int(content_length_str), MAX_BODY_SIZE)
                request_body = environ["wsgi.input"].read(length)
            except (ValueError, TypeError):
                # Handle cases where CONTENT_LENGTH is invalid or input stream is problematic.
                # In a production setup, consider logging this error.
                pass

        # Queues for inter-thread communication:
        # `start_queue`: Transmits HTTP status and headers from ASGI thread to WSGI thread.
        # `chunk_queue`: Transmits response body chunks from ASGI thread to WSGI thread.
        start_queue: StartQueue = queue.SimpleQueue()
        chunk_queue: ChunkQueue = queue.SimpleQueue()

        # Submit the ASGI application execution to the thread pool.
        # The `_run_asgi_in_thread` method will manage the ASGI lifecycle.
        future = self.executor.submit(
            self._run_asgi_in_thread,
            scope,
            request_body,
            start_queue,
            chunk_queue,
        )

        # Block the current WSGI thread until the ASGI application sends the
        # `http.response.start` message, indicating that HTTP status and headers are ready.
        # This ensures `start_response` is called only after the headers are determined.
        future.result()

        # Retrieve the status line and WSGI-formatted headers from the start queue.
        status, wsgi_headers = start_queue.get()

        def response_stream() -> Iterable[bytes]:
            """
            Generator function that yields response body chunks.
            This function is iterated by the WSGI server to stream the response.
            It continuously fetches chunks from the `chunk_queue` until a `None`
            sentinel is received, signaling the end of the response body.
            """
            while True:
                chunk = chunk_queue.get()
                if chunk is None:  # Sentinel value indicating end of stream
                    break
                yield chunk

        # Call the WSGI `start_response` callable with the received status and headers.
        start_response(status, wsgi_headers)

        # Return the generator. The WSGI server will iterate over this to
        # send the response body chunks to the client.
        return response_stream()

    def _run_asgi_in_thread(
        self,
        scope: Scope,
        request_body: bytes,
        start_queue: StartQueue,
        chunk_queue: ChunkQueue,
    ) -> None:
        """
        Executes the ASGI application within a dedicated thread. This method is designed
        to be run by `concurrent.futures.ThreadPoolExecutor`. It sets up an independent
        asyncio event loop for the ASGI application and defines the `send` and `receive`
        callables required by the ASGI specification. These callables bridge the
        asynchronous ASGI communication with synchronous Python queues, allowing
        data transfer between the ASGI application thread and the main WSGI thread.

        Args:
            scope: The ASGI scope dictionary for the current request.
            request_body: The complete request body as bytes.
            start_queue: A `SimpleQueue` to transmit the HTTP status and headers
                         (from `http.response.start` messages) back to the WSGI thread.
            chunk_queue: A `SimpleQueue` to transmit response body chunks
                         (from `http.response.body` messages) back to the WSGI thread.
                         A `None` sentinel is sent to indicate the end of the response body.
        """
        # Ensure that each thread has its own asyncio event loop.
        # If no loop is set for this thread or if the existing one is closed,
        # a new event loop is created and set as the current one for this thread.
        if not hasattr(_thread_local, "loop") or _thread_local.loop.is_closed():
            _thread_local.loop = asyncio.new_event_loop()
        loop = _thread_local.loop
        asyncio.set_event_loop(loop)

        async def send(message: Message) -> None:
            """
            Implements the ASGI 'send' callable.
            This asynchronous function processes messages from the ASGI application
            and puts them into the appropriate queues for the WSGI thread.
            """
            if message["type"] == "http.response.start":
                # Extract status code and headers from the ASGI 'start' message.
                status_code = message["status"]
                status = f"{status_code} {HTTPStatus(status_code).phrase}"

                # Decode headers from bytes to strings for WSGI compliance.
                wsgi_headers: list[tuple[str, str]] = [
                    (k.decode(ENCODING), v.decode(ENCODING))
                    for k, v in message["headers"]
                ]
                start_queue.put((status, wsgi_headers))
            elif message["type"] == "http.response.body":
                # Send body chunks. If 'body' is missing, default to empty bytes.
                if body := message.get("body", b""):
                    chunk_queue.put(body)
                # If 'more_body' is explicitly False, or not present (defaults to False),
                # it signals the end of the response body stream.
                if not message.get("more_body", False):
                    chunk_queue.put(None)  # Sentinel for end of stream

        async def receive() -> Message:
            """
            Implements the ASGI 'receive' callable.
            For HTTP requests, this provides the request body to the ASGI application.
            In this adapter, the entire request body is read upfront by the WSGI server
            and provided at once.
            """
            # For HTTP requests, the body is typically fully available from the WSGI server
            # at the start. 'more_body: False' indicates that no further body parts will follow.
            return {
                "type": "http.request",
                "body": request_body,
                "more_body": False,
            }

        try:
            # Run the ASGI application's main coroutine to completion within this thread's event loop.
            loop.run_until_complete(self.app(scope, receive, send))
        except Exception:
            # If an unhandled error occurs in the ASGI application, print the traceback
            # and attempt to send a 500 Internal Server Error response if headers
            # haven't already been sent.
            traceback.print_exc(file=sys.stderr)
            if start_queue.empty():
                # Only send a 500 error if the `http.response.start` message hasn't been processed yet.
                start_queue.put(
                    (
                        "500 Internal Server Error",
                        [("Content-Type", "text/plain")],
                    )
                )
                chunk_queue.put(
                    b"Internal Server Error: An unexpected error occurred in the ASGI application."
                )
            chunk_queue.put(
                None
            )  # Ensure the response stream terminates, even on error.
        finally:
            # Attempt to shut down async generators and close the event loop.
            # This helps prevent resource leaks, especially in a thread pool.
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                # Ignore exceptions during shutdown, as the loop might already be closing
                # or in an inconsistent state, which is acceptable during cleanup.
                pass
