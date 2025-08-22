# ASGI2WSGI

`asgi2wsgi` is a lightweight Python module designed to bridge the gap between ASGI (Asynchronous Server Gateway Interface) applications, such as those built with FastAPI or Starlette, and traditional WSGI (Web Server Gateway Interface) environments like Gunicorn, Apache with mod_wsgi, or cPanel's Passenger. This adapter enables you to run your modern asynchronous ASGI applications seamlessly within existing WSGI server infrastructures.

It functions by wrapping an ASGI application, translating incoming WSGI requests into the ASGI "scope" and "receive" callable, and then transforming the ASGI "send" messages back into WSGI responses. The asynchronous nature of the ASGI application is managed by executing it within a dedicated internal thread pool, where each thread maintains its own independent asyncio event loop. This ensures efficient concurrent handling of requests, with the response body streamed asynchronously as chunks.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Example for cPanel (passenger_wsgi.py)](#example-for-cpanel-passenger_wsgipy)
- [Contributing](#contributing)
- [License](#license)
- [Author](#author)
- [Origin](#origin)

## Features

`asgi2wsgi` provides a robust and efficient solution for integrating ASGI applications into WSGI servers:

- **Broad ASGI Framework Compatibility**: Designed to work seamlessly with any ASGI 3.0 compliant application, including popular frameworks like FastAPI and Starlette.
- **Performance Optimized**: Engineered for minimal overhead, efficiently translating between protocols to maintain application performance.
- **Robust Type Safety**: Implemented with strict type annotations, leveraging Python 3.12+ syntax for clarity and maintainability.
- **Memory Safety**: Includes a built-in cap on the maximum request body size (currently 10 MB) to prevent excessive memory consumption and enhance stability, protecting your server from large malicious inputs, especially when the underlying WSGI server might buffer the entire body.
- **Configurable Logging**: Integrates standard Python logging, allowing you to easily configure log levels, format, and stream for debugging and monitoring.
- **Easy Integration**: Provides a straightforward way to deploy ASGI applications in existing WSGI server setups with minimal configuration.

## Installation

`asgi2wsgi` can be installed using [uv](https://github.com/astral-sh/uv) from its Git repository:

```bash
uv add git+https://github.com/RakibulHasanRatul/asgi2wsgi
```

Despite the `uv` installation option, developers can also simply copy and paste the module. `asgi2wsgi` is designed as a single-file module, meaning it does not strictly require `pip` or `uv` installation. To integrate it into your project, simply copy the content of the [`asgi2wsgi/__init__.py`](./asgi2wsgi/__init__.py) file into your codebase. Once copied, you can import the `ASGI2WSGI` class and wrap your existing ASGI application with it.

## Usage

This module is designed to be broadly compatible with any ASGI application that adheres to the ASGI 3.0 specification.

**Note on Compatibility**: While general-purpose ASGI 3.0 compliant, `asgi2wsgi` has been primarily optimized and tested with FastAPI applications. Compatibility with other ASGI frameworks should be thoroughly tested in your specific environment.

The `ASGI2WSGI` constructor accepts the following arguments:

- `app`: The ASGI application callable (e.g., an instance of FastAPI, Starlette). It must adhere to the ASGI 3.0 specification.
- `num_workers`: (Optional) The number of threads in the internal thread pool used to execute the asynchronous ASGI application for each concurrent WSGI request. Defaults to `1`. Adjust this value based on your concurrency needs and available resources. A higher number increases potential concurrency.
- `log_format`: (Optional) A string to configure the log format for the adapter's internal logger. Defaults to `"%(asctime)s - %(levelname)s - %(name)s - %(message)s"`.
- `log_stream`: (Optional) The stream to which log messages will be written. Defaults to `sys.stderr`.

```python
from fastapi import FastAPI # Example: Your ASGI framework import
from asgi2wsgi import ASGI2WSGI # Assuming asgi2wsgi.py is in your project

# Your ASGI application instance
my_asgi_app = FastAPI() # Replace with your actual ASGI app initialization

@my_asgi_app.get("/")
async def read_root():
    return {"message": "Hello from ASGI!"}

# Wrap your ASGI application for deployment in a WSGI environment
# The default num_workers is 1, but you can increase it for more concurrency:
application = ASGI2WSGI(my_asgi_app, num_workers=4)

# The 'application' object is now a WSGI callable that can be served by any WSGI server
# (e.g., Gunicorn, Apache with mod_wsgi, cPanel's Passenger)
```

### Example for cPanel (passenger_wsgi.py)

For deployments on cPanel, you typically use a `passenger_wsgi.py` file in your application's root directory. Here's an example of how to configure it:

```python
import os
import sys

# Add your application's directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI # Replace with your actual ASGI framework import
from asgi2wsgi import ASGI2WSGI

# Initialize your ASGI application
app = FastAPI() # Replace with your actual ASGI app initialization

@app.get("/")
async def read_root():
    return {"message": "Hello from ASGI on cPanel!"}

# Create the WSGI application instance
application = ASGI2WSGI(app)
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Author

Rakibul Hasan Ratul <rakibulhasanratul@proton.me>
Independent Developer, Dhaka, Bangladesh

## Origin

The core logic of this module is inspired by and adapted from the original `asgi2wsgi` project by Tiangolo (which is available at [https://github.com/tiangolo/asgi2wsgi](https://github.com/tiangolo/asgi2wsgi)). This version has been further optimized and tweaked to enhance its compatibility and performance with popular ASGI frameworks.
