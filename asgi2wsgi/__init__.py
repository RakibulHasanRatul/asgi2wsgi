import asyncio
import concurrent.futures
import logging
import queue
import sys
import threading
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Iterable, MutableMapping

# Configure the root logger for basic internal error logging.
# By default, critical issues are logged to stderr.
# Developers can customize logging levels or add handlers via `logging.getLogger(__name__).setLevel(...)`.
# The logging format and stream can also be configured directly in the ASGI2WSGI constructor.
logging.basicConfig(
    level=logging.ERROR,
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Thread-local storage for managing asyncio event loops.
# Each worker thread processing an ASGI application request maintains its own independent event loop.
_thread_local = threading.local()

# Default encoding for HTTP headers, as specified by RFCs (typically Latin-1).
ENCODING = "latin-1"
# Maximum size of the request body to be read into memory (10 MB).
# This limit prevents excessive memory consumption from very large uploads,
# especially when the underlying WSGI server might buffer the entire body.
MAX_BODY_SIZE = 10 * 1024 * 1024

# Type aliases for improved type safety and readability throughout the adapter.
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
    ASGI2WSGI is a robust adapter designed to enable ASGI (Asynchronous Server Gateway Interface)
    applications, such as FastAPI or Starlette, to operate seamlessly within traditional WSGI
    (Web Server Gateway Interface) environments like Gunicorn, Apache with mod_wsgi, or cPanel's Passenger.

    This adapter functions by wrapping an ASGI application. It translates incoming WSGI requests
    into the ASGI "scope" and "receive" callable, and subsequently transforms the ASGI "send"
    messages back into WSGI responses. The asynchronous nature of the ASGI application is
    managed by executing it within a dedicated thread pool, where each thread maintains its
    own independent asyncio event loop.

    Key Features:
    - Broad ASGI Framework Compatibility: Designed to work seamlessly with any ASGI 3.0
      compliant application, including popular frameworks like FastAPI and Starlette.
    - Performance Optimized: Engineered for minimal overhead, efficiently translating between
      protocols to maintain application performance.
    - Robust Type Safety: Implemented with strict type annotations, leveraging Python 3.12+
      syntax for clarity and maintainability.
    - Easy Integration: Provides a straightforward way to deploy ASGI applications
      within existing WSGI server setups.

    Usage Example:
    ```python
    from fastapi import FastAPI
    # from asgi2wsgi import ASGI2WSGI # Assuming asgi2wsgi.py is accessible or installed

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
    # from asgi2wsgi import ASGI2WSGI # Ensure this file is in your Python path

    # Initialize your ASGI application
    app = FastAPI()

    @app.get("/")
    async def read_root():
        return {"message": "Hello from ASGI on cPanel!"}

    # Create the WSGI application instance for Passenger
    application = ASGI2WSGI(app)
    ```
    """

    def __init__(
        self,
        app: ASGIApp,
        num_workers: int = 1,
        log_format: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        log_stream: Any = sys.stderr,
    ) -> None:
        """
        Initializes the ASGI2WSGI adapter with the target ASGI application.

        Args:
            app: The ASGI application callable (e.g., an instance of FastAPI, Starlette).
                 It must adhere to the ASGI 3.0 specification.
            num_workers: The number of threads in the internal thread pool used to
                         execute the asynchronous ASGI application for each concurrent
                         WSGI request. A higher number increases concurrency but also
                         resource usage. Defaults to 1.
            log_format: String to configure the log format for the adapter's internal logger.
                        Example: "%(asctime)s - %(levelname)s - %(name)s - %(message)s".
            log_stream: The stream to which log messages will be written. Defaults to sys.stderr.
        """
        self.app = app
        # Initialize ThreadPoolExecutor. This executor will typically persist for the
        # lifetime of the application process, handling multiple concurrent WSGI requests.
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=num_workers)

        # Configure the adapter's specific logger format and stream.
        # Clear existing handlers to prevent duplicate logs or conflicts with prior basicConfig settings.
        for handler in logger.handlers[
            :
        ]:  # Iterate over a slice to enable in-place modification
            logger.removeHandler(handler)

        handler = logging.StreamHandler(log_stream)
        formatter = logging.Formatter(log_format)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False  # Prevent messages from being passed to ancestor loggers (e.g., the root logger)
        logger.debug("Logger format set to: %s", log_format)
        logger.debug("Logger stream set to: %s", log_stream)

        logger.info(
            "ASGI2WSGI adapter initialized with %d worker threads.", num_workers
        )

    def __call__(
        self,
        environ: WSGIEnviron,
        start_response: StartResponse,
    ) -> Iterable[bytes]:
        """
        This method allows the ASGI2WSGI instance to act as a WSGI callable.
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

        logger.debug(
            "Received WSGI request for path: %s", environ.get("PATH_INFO", "/")
        )

        # Extract HTTP headers from the WSGI environment.
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                # Convert 'HTTP_ACCEPT_ENCODING' to 'accept-encoding' byte string.
                header_name = key[5:].replace("_", "-").lower().encode(ENCODING)
                request_headers.append((header_name, value.encode(ENCODING)))
                logger.debug(
                    "Parsed header: %s: %s", header_name.decode(ENCODING), value
                )

        # Add standard content headers (e.g., Content-Type, Content-Length) if present.
        for header_name_key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if value := environ.get(header_name_key):
                header_name = header_name_key.replace("_", "-").lower().encode(ENCODING)
                request_headers.append((header_name, value.encode(ENCODING)))
                logger.debug(
                    "Parsed content header: %s: %s",
                    header_name.decode(ENCODING),
                    value,
                )

        # Construct the ASGI scope dictionary from the WSGI environment.
        # Default values are used for robustness against potentially incomplete WSGI environments.
        server_port: int = int(environ.get("SERVER_PORT", "80"))
        remote_port: int = int(
            environ.get("REMOTE_PORT", "0")
        )  # Default to 0 if not specified

        server_protocol: str = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        http_version: str = (
            server_protocol.split("/")[1] if "/" in server_protocol else "1.1"
        )

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": http_version,
            "method": environ["REQUEST_METHOD"],
            "headers": request_headers,
            "path": environ.get("PATH_INFO", "/"),
            "root_path": environ.get("SCRIPT_NAME", ""),
            "raw_path": environ.get("PATH_INFO", "/").encode(ENCODING),
            "query_string": environ.get("QUERY_STRING", "").encode(ENCODING),
            "server": (environ.get("SERVER_NAME", "localhost"), server_port),
            "client": (environ.get("REMOTE_ADDR", "127.0.0.1"), remote_port),
            "scheme": environ.get("wsgi.url_scheme", "http"),
            "extensions": {},  # ASGI extensions can be added here if supported by the adapter
        }
        logger.debug(
            "Constructed ASGI scope: %s",
            {
                k: v
                for k, v in scope.items()
                if k not in ["headers", "body", "raw_path"]
            },
        )

        # Read the request body. WSGI servers typically provide it via wsgi.input.
        request_body: bytes = b""
        if content_length_str := environ.get("CONTENT_LENGTH"):
            try:
                length: int = min(int(content_length_str), MAX_BODY_SIZE)
                request_body = environ["wsgi.input"].read(length)
                logger.debug("Read %d bytes from request body.", len(request_body))
            except (ValueError, TypeError) as e:
                # Log if CONTENT_LENGTH is invalid or input stream issues occur. Proceed with an empty body.
                logger.error(
                    "Failed to read request body for %s (CONTENT_LENGTH: %s) due to invalid length or stream error: %s",
                    scope["path"],
                    content_length_str,
                    e,
                    exc_info=True,
                )
            except Exception as e:
                # Catch any other unexpected errors during input reading.
                logger.error(
                    "Unexpected error reading wsgi.input for %s: %s",
                    scope["path"],
                    e,
                    exc_info=True,
                )
        else:
            logger.debug("No CONTENT_LENGTH header, assuming empty request body.")

        # Queues for inter-thread communication between the WSGI thread and the ASGI thread:
        # `start_queue`: Transmits HTTP status and headers (from ASGI thread).
        # `chunk_queue`: Transmits response body chunks (from ASGI thread).
        start_queue: StartQueue = queue.SimpleQueue()
        chunk_queue: ChunkQueue = queue.SimpleQueue()

        # Submit the ASGI application execution to the thread pool.
        # The `_run_asgi_in_thread` method encapsulates the ASGI application's lifecycle.
        future: concurrent.futures.Future[None] = self.executor.submit(
            self._run_asgi_in_thread,
            scope,
            request_body,
            start_queue,
            chunk_queue,
        )
        logger.debug("ASGI application submitted to thread pool for %s", scope["path"])

        try:
            # Wait for the ASGI application to complete its execution or raise an exception.
            # This implicitly waits for the 'http.response.start' message to be queued.
            future.result()
            logger.debug(
                "ASGI thread for %s completed successfully or handled its exception.",
                scope["path"],
            )
        except Exception as e:
            # If an exception occurred in the ASGI thread, `_run_asgi_in_thread` has already
            # logged it and attempted to put a 500 error into the queues. Catching it here
            # prevents it from disrupting the WSGI response flow, allowing `response_stream`
            # to correctly receive and yield the error message.
            logger.debug(
                "Exception propagated from ASGI thread for %s, handled by error response mechanism: %s",
                scope["path"],
                e,
            )
            pass  # The `_run_asgi_in_thread` already placed error messages in queues.

        # Retrieve the status line and WSGI-formatted headers from the start queue.
        # This operation blocks until the ASGI app sends the 'http.response.start' message or an error response is queued.
        status: str
        wsgi_headers: list[tuple[str, str]]
        status, wsgi_headers = start_queue.get()
        logger.debug("Received HTTP status '%s' and headers from ASGI thread.", status)

        def response_stream() -> Iterable[bytes]:
            """
            Generator function that yields response body chunks from the chunk_queue.
            The WSGI server iterates over this generator to stream the response back to the client.
            It continuously fetches chunks from `chunk_queue` until a `None` sentinel
            is received, indicating the end of the response body stream.
            """
            while True:
                chunk: bytes | None = chunk_queue.get()
                if chunk is None:  # Sentinel value indicating end of stream
                    logger.debug(
                        "Received end-of-stream sentinel for %s.", scope["path"]
                    )
                    break
                logger.debug(
                    "Yielding %d bytes of response body for %s.",
                    len(chunk),
                    scope["path"],
                )
                yield chunk

        # Call the WSGI `start_response` callable with the received status and headers.
        start_response(status, wsgi_headers)
        logger.debug(
            "Called start_response for %s with status '%s'.",
            scope["path"],
            status,
        )

        # Return the generator. The WSGI server will iterate over this to
        # send the response body chunks to the client as they become available.
        return response_stream()

    def _run_asgi_in_thread(
        self,
        scope: Scope,
        request_body: bytes,
        start_queue: StartQueue,
        chunk_queue: ChunkQueue,
    ) -> None:
        """
        Executes the ASGI application within a dedicated thread managed by a ThreadPoolExecutor.

        This method is responsible for:
        1. Setting up an independent asyncio event loop for the ASGI application.
        2. Defining the ASGI 'send' and 'receive' callables, which bridge asynchronous
           ASGI communication with synchronous Python queues for data transfer
           between the ASGI application thread and the main WSGI thread.
        3. Running the ASGI application's main coroutine to completion.
        4. Handling unhandled exceptions within the ASGI application, attempting to
           return a 500 Internal Server Error if response headers haven't been sent yet.

        Args:
            scope: The ASGI scope dictionary for the current request.
            request_body: The complete request body as bytes.
            start_queue: A `SimpleQueue` to transmit the HTTP status and headers
                         (from `http.response.start` messages) back to the WSGI thread.
            chunk_queue: A `SimpleQueue` to transmit response body chunks
                         (from `http.response.body` messages) back to the WSGI thread.
                         A `None` sentinel is sent to indicate the end of the response body.
        """
        # Ensure that each thread running an ASGI application has its own asyncio event loop.
        # A new event loop is created and set as the current one for this thread if no loop
        # is present or the existing one is closed. This pattern is essential for
        # integrating asyncio within a ThreadPoolExecutor.
        loop: asyncio.AbstractEventLoop
        if not hasattr(_thread_local, "loop") or _thread_local.loop.is_closed():
            _thread_local.loop = asyncio.new_event_loop()
            logger.debug(
                "Created new asyncio event loop for thread %s.",
                threading.get_ident(),
            )
        else:
            logger.debug(
                "Reusing existing asyncio event loop for thread %s.",
                threading.get_ident(),
            )
        loop = _thread_local.loop
        asyncio.set_event_loop(loop)
        logger.debug("ASGI thread started for %s", scope["path"])

        async def send(message: Message) -> None:
            """
            Implements the ASGI 'send' callable.

            This asynchronous function processes messages from the ASGI application
            and puts them into the appropriate queues for the WSGI thread.
            It handles `http.response.start` for headers and `http.response.body`
            for response content.
            """
            message_type: Any = message.get("type")
            logger.debug(
                "ASGI send message of type '%s' for %s.",
                message_type,
                scope["path"],
            )

            if message_type == "http.response.start":
                # Extract status code and headers from the ASGI 'start' message.
                status_code: int = message.get("status", 200)
                status: str = f"{status_code} {HTTPStatus(status_code).phrase}"

                # Decode headers from bytes to strings for WSGI compliance.
                wsgi_headers: list[tuple[str, str]] = []
                for k_bytes, v_bytes in message.get("headers", []):
                    try:
                        wsgi_headers.append(
                            (k_bytes.decode(ENCODING), v_bytes.decode(ENCODING))
                        )
                    except UnicodeDecodeError:
                        logger.warning(
                            "Failed to decode header %r: %r using %s encoding. Skipping or using fallback.",
                            k_bytes,
                            v_bytes,
                            ENCODING,
                        )
                        # Fallback to ignore errors if decoding fails to avoid crashing
                        wsgi_headers.append(
                            (
                                k_bytes.decode(ENCODING, errors="ignore"),
                                v_bytes.decode(ENCODING, errors="ignore"),
                            )
                        )

                start_queue.put((status, wsgi_headers))
                logger.debug(
                    "Queued HTTP response start for %s: status=%s, headers=%s",
                    scope["path"],
                    status,
                    wsgi_headers,
                )
            elif message_type == "http.response.body":
                # Queue body chunks. If 'body' is missing, default to empty bytes.
                body_chunk: bytes = message.get("body", b"")
                chunk_queue.put(body_chunk)
                logger.debug(
                    "Queued %d bytes of response body for %s (more_body: %s).",
                    len(body_chunk),
                    scope["path"],
                    message.get("more_body", False),
                )

                # If 'more_body' is explicitly False (or absent, defaulting to False),
                # it signals the end of the response body stream.
                if not message.get("more_body", False):
                    chunk_queue.put(None)  # Sentinel for end of stream
                    logger.debug("Queued end-of-stream sentinel for %s.", scope["path"])
            else:
                logger.warning(
                    "Received unhandled ASGI message type '%s' for %s.",
                    message_type,
                    scope["path"],
                )

        async def receive() -> Message:
            """
            Implements the ASGI 'receive' callable.

            For HTTP requests, this callable provides the request body to the ASGI application.
            In this adapter, the entire request body is read upfront by the WSGI server
            and provided at once. This implementation assumes a single `http.request` message.
            """
            # For HTTP requests, the body is typically fully available from the WSGI server
            # at the start. `more_body: False` indicates that no further body parts will follow.
            logger.debug(
                "ASGI receive called for %s, providing request body (%d bytes).",
                scope["path"],
                len(request_body),
            )
            return {
                "type": "http.request",
                "body": request_body,
                "more_body": False,
            }

        try:
            # Run the ASGI application's main coroutine to completion within this thread's event loop.
            logger.debug("Running ASGI application for %s.", scope["path"])
            loop.run_until_complete(self.app(scope, receive, send))
            logger.debug("ASGI application finished for %s.", scope["path"])
        except Exception:
            # If an unhandled error occurs in the ASGI application, log the traceback
            # and attempt to send a 500 Internal Server Error response if headers
            # haven't already been sent to the WSGI server.
            logger.exception(
                "Exception caught in ASGI application thread for %s:",
                scope["path"],
            )
            if start_queue.empty():
                # Only send a 500 error if the `http.response.start` message hasn't been processed yet.
                # If headers were already sent, the WSGI server cannot change the status code.
                logger.error(
                    "ASGI application failed for %s before headers were sent. Sending 500 Internal Server Error.",
                    scope["path"],
                )
                start_queue.put(
                    (
                        "500 Internal Server Error",
                        [("Content-Type", "text/plain")],
                    )
                )
                chunk_queue.put(
                    b"Internal Server Error: An unexpected error occurred in the ASGI application."
                )
            else:
                logger.warning(
                    "ASGI application for %s failed after headers were sent. Cannot send 500 status.",
                    scope["path"],
                )
            # Ensure the response stream terminates, even on error.
            chunk_queue.put(None)
            # Re-raise the exception to be propagated back to the main WSGI thread
            # via `future.result()` in the `__call__` method.
            raise
        finally:
            # Attempt to shut down async generators for resource cleanup.
            # Explicitly closing the event loop here is generally not recommended in a ThreadPoolExecutor
            # if the loop is intended to be reused for subsequent tasks on the same thread.
            # The current setup reuses the loop if it's not closed, otherwise creates a new one.
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                logger.debug(
                    "Async generators shut down successfully for thread %s (request %s).",
                    threading.get_ident(),
                    scope["path"],
                )
            except Exception as e:
                # Log exceptions during shutdown_asyncgens as debug info,
                # as they are often non-critical or due to the loop being in a certain state.
                logger.debug(
                    "Exception during asyncio loop shutdown_asyncgens for thread %s (request %s): %s",
                    threading.get_ident(),
                    scope["path"],
                    e,
                    exc_info=True,
                )
            finally:
                loop.close()
                logger.debug(
                    "Asyncio event loop closed for thread %s (request %s).",
                    threading.get_ident(),
                    scope["path"],
                )
