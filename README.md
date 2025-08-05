# ASGI2WSGI

`asgi2wsgi` is a Python module designed to bridge the gap between ASGI (Asynchronous Server Gateway Interface) applications, specifically FastAPI, and traditional WSGI (Web Server Gateway Interface) environments like cPanel. This module allows you to run your FastAPI applications in WSGI-compatible servers, providing a seamless integration.

## Features

- **FastAPI Compatibility**: Specifically tweaked to work efficiently with FastAPI applications.
- **Performance Optimized**: Developed with performance in mind, ensuring minimal overhead when converting ASGI to WSGI.
- **Type Safety**: Written with absolute type safety, leveraging Python 3.12's features for robust and maintainable code.
- **Easy Integration**: Simple to integrate into your existing WSGI setup.

## Usage

To integrate `asgi2wsgi` into your project, simply copy the content of the `asgi2wsgi.py` file into your codebase. Once copied, you can import the `ASGI2WSGI` class and wrap your existing ASGI application with it.

This module is designed to be compatible with any ASGI application that adheres to the ASGI 3.0 specification.

⚠ **_Note:_** _This module has only been tested with FastAPI. Compatibility with other Starlette-based ASGI applications is not yet thoroughly tested._

```python
from fastapi import FastAPI # Example: Your ASGI framework import
from asgi2wsgi import ASGI2WSGI # Assuming asgi2wsgi.py is in your project

# Your ASGI application instance
my_asgi_app = FastAPI() # Replace with your actual ASGI app initialization

@my_asgi_app.get("/")
async def read_root():
    return {"message": "Hello from ASGI!"}

# Wrap your ASGI application for deployment in a WSGI environment
application = ASGI2WSGI(my_asgi_app)

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

## Author

Rakibul Hasan Ratul <rakibulhasanratul@proton.me>  
Independent Developer, Dhaka, Bangladesh

## Origin

The core logic of this module is inspired by and adapted from the original `asgi2wsgi` project by Tiangolo (may available at [https://github.com/tiangolo/asgi2wsgi](https://github.com/tiangolo/asgi2wsgi)). This version has been further optimized and tweaked to specifically enhance its compatibility and performance with FastAPI applications.
