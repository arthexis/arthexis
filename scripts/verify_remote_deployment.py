"""Deployment acceptance checks for Watchtower remote access."""

import argparse
import json
import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ORIGIN = "https://remote.arthexis.com"
RESOURCE = f"{ORIGIN}/mcp"
PROTECTED = f"{ORIGIN}/.well-known/oauth-protected-resource/mcp"
AUTH_SERVER = f"{ORIGIN}/.well-known/oauth-authorization-server"


def _wait_listener(host: str, port: int, *, attempts: int = 40) -> None:
    import time

    last = None
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as error:
            last = error
            time.sleep(0.25)
    raise RuntimeError(f"service did not open {host}:{port}: {last}")


def verify_local() -> None:
    _wait_listener("127.0.0.1", 8000)
    _wait_listener("127.0.0.1", 8001)


def _json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _expect_http_error(request: Request, status: int) -> HTTPError:
    try:
        urlopen(request, timeout=10)
    except HTTPError as error:
        if error.code != status:
            raise RuntimeError(
                f"{request.full_url} returned {error.code}, expected {status}"
            ) from error
        return error
    raise RuntimeError(f"{request.full_url} unexpectedly succeeded")


def verify_public() -> None:
    protected = _json(PROTECTED)
    if protected.get("resource") != RESOURCE:
        raise RuntimeError("protected-resource metadata has wrong resource")
    if protected.get("authorization_servers") != [ORIGIN]:
        raise RuntimeError("protected-resource metadata has wrong issuer")

    authorization = _json(AUTH_SERVER)
    expected = {
        "issuer": ORIGIN,
        "authorization_endpoint": f"{ORIGIN}/oauth/authorize",
        "token_endpoint": f"{ORIGIN}/oauth/token",
        "revocation_endpoint": f"{ORIGIN}/oauth/revoke",
        "code_challenge_methods_supported": ["S256"],
    }
    for key, value in expected.items():
        if authorization.get(key) != value:
            raise RuntimeError(f"authorization metadata mismatch for {key}")

    authorize = _expect_http_error(
        Request(f"{ORIGIN}/oauth/authorize", method="GET"),
        400,
    )
    authorize.read()

    token = _expect_http_error(
        Request(f"{ORIGIN}/oauth/token", method="GET"),
        405,
    )
    token.read()

    mcp = _expect_http_error(Request(RESOURCE, method="GET"), 401)
    challenge = mcp.headers.get("WWW-Authenticate", "")
    mcp.read()
    if f'resource_metadata="{PROTECTED}"' not in challenge:
        raise RuntimeError("MCP challenge does not advertise protected-resource metadata")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("local", "public"))
    args = parser.parse_args()
    if args.mode == "local":
        verify_local()
    else:
        verify_public()


if __name__ == "__main__":
    main()
