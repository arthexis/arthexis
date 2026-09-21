"""Readiness probe used by Watchtower deployment recipes."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def ready(
    host: str = "127.0.0.1",
    port: int = 8888,
    domain: str = "arthexis.com",
    path: str = "/health/",
    timeout: float = 2.0,
) -> bool:
    """Return whether the local Arthexis health endpoint is ready."""
    request = Request(
        f"http://{host}:{int(port)}{path}",
        headers={"Host": domain},
    )
    try:
        with urlopen(request, timeout=float(timeout)) as response:
            if response.status != 200:
                return False
            payload = json.load(response)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError):
        return False
    return payload == {"status": "ok"}
