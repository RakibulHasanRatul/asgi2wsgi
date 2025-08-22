import queue
import sys
from typing import Any, Awaitable, Callable, MutableMapping

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
