# ASGI2WSGI

`asgi2wsgi` is a Python module designed to bridge the gap between ASGI (Asynchronous Server Gateway Interface) applications, specifically FastAPI, and traditional WSGI (Web Server Gateway Interface) environments like cPanel. This module allows you to run your FastAPI applications in WSGI-compatible servers, providing a seamless integration.

## Features

- **FastAPI Compatibility**: Specifically tweaked to work efficiently with FastAPI applications.
- **Performance Optimized**: Developed with performance in mind, ensuring minimal overhead when converting ASGI to WSGI.
- **Type Safety**: Written with absolute type safety, leveraging Python 3.12's features for robust and maintainable code.
- **Easy Integration**: Simple to integrate into your existing WSGI setup.

## Usage

To use `asgi2wsgi`, simply copy the content of the [`asgi2wsgi.py`](./asgi2wsgi.py) file into your project's codebase. Then, you can import the `ASGI2WSGI` class and wrap your ASGI application with it.

**_This module is designed to work with any ASGI application that is based on Starlette._**

⚠️ **_Note:_** _This module has only been tested with FastAPI. Compatibility with other Starlette-based ASGI applications is not yet thoroughly tested._

```python
from fastapi import FastAPI # or any other Starlette-based ASGI framework
from asgi2wsgi import ASGI2WSGI # asuming the file was named asgi2wsgi.py contains ASGI2WSGI

# Your ASGI application (e.g., FastAPI app)
my_asgi_app = FastAPI()

@my_asgi_app.get("/")
async def read_root():
    return {"Hello": "World"}

# Wrap your ASGI app for WSGI environments
application = ASGI2WSGI(my_asgi_app)

# Now, 'application' can be used in your WSGI server configuration
# (e.g., in a `passenger_wsgi.py` file for cPanel)
```

### Example for cPanel (passenger_wsgi.py)

If you are deploying on cPanel, you would typically have a `passenger_wsgi.py` file in your application's root directory. The content of this file would look something like this:

```python
import os
import sys

# Add your application's directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI # or your Starlette-based ASGI app
from asgi2wsgi import ASGI2WSGI

# Initialize your ASGI app
app = FastAPI()

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
