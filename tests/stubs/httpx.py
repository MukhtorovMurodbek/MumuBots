class AsyncClient:
    def __init__(self, *a, **k): pass
class Timeout:
    def __init__(self, *a, **k): pass
class Limits:
    def __init__(self, *a, **k): pass
class HTTPError(Exception): pass
class HTTPStatusError(HTTPError): pass
class RequestError(HTTPError): pass
