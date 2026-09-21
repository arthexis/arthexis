"""Deployment readiness checks for Arthexis."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def local(
    host: str = "127.0.0.1",
    port: int = 8888,
    domain: str = "arthexis.com",
    path: str = "/health/",
    timeout: float = 2.0,
) -> bool:
    """Return whether the local HTTP health endpoint is ready."""
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


def main(
    *,
    local: bool = False,
    host: str = "127.0.0.1",
    port: int = 8888,
    domain: str = "arthexis.com",
    path: str = "/health/",
    timeout: float = 2.0,
) -> bool:
    """Run the selected readiness checks.

    The unscoped comprehensive readiness suite is intentionally not defined yet.
    Use --local for the current Watchtower startup/readiness check.
    """
    if not local:
        raise ValueError("ready currently requires --local")
    return globals()["local"](
        host=host,
        port=port,
        domain=domain,
        path=path,
        timeout=timeout,
    )
