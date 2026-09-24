"""Deployment acceptance checks for Watchtower remote access."""

import argparse
import json
import socket
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gway.security.scopes import ScopeRegistry
from gway.security.tokens import TokenRegistry

ORIGIN = "https://remote.arthexis.com"
RESOURCE = f"{ORIGIN}/mcp"
PROTECTED = f"{ORIGIN}/.well-known/oauth-protected-resource/mcp"
AUTH_SERVER = f"{ORIGIN}/.well-known/oauth-authorization-server"
ACCEPTANCE_CLIENT = f"{ORIGIN}/.well-known/gway-acceptance-client"
SECURITY_STATE = "/var/lib/gway/cache/security/state.sqlite"


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


def _authorized_json(url: str, bearer: str) -> tuple[dict, object]:
    request = Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8")), response.headers


def _decode_mcp(body: bytes, content_type: str) -> dict:
    text = body.decode("utf-8")
    if str(content_type).startswith("application/json"):
        return json.loads(text)
    if "text/event-stream" in str(content_type):
        messages = []
        for line in text.splitlines():
            if line.startswith("data:"):
                messages.append(json.loads(line[5:].strip()))
        if not messages:
            raise RuntimeError("MCP response contained no data event")
        return messages[-1]
    raise RuntimeError(f"Unsupported MCP response content type: {content_type}")


def _mcp_post(
    bearer: str,
    payload: dict,
    *,
    session_id: str | None = None,
    protocol_version: str | None = None,
) -> tuple[int, dict, object]:
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = Request(
        RESOURCE,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urlopen(request, timeout=10) as response:
        body = response.read()
        if not body:
            return response.status, {}, response.headers
        return (
            response.status,
            _decode_mcp(body, response.headers.get("Content-Type", "")),
            response.headers,
        )


def _verify_public_query(bearer: str) -> None:
    payload, headers = _authorized_json(
        f"{ORIGIN}/query?" + urlencode({"c": "log sources"}),
        bearer,
    )
    if "result" not in payload or not isinstance(payload["result"], list):
        raise RuntimeError("public query did not return log source results")
    if headers.get("Cache-Control", "").casefold() != "no-store":
        raise RuntimeError("public query is missing Cache-Control: no-store")


def _verify_public_mutation_ceiling(bearer: str) -> None:
    request = Request(
        f"{ORIGIN}/query?" + urlencode({"c": "clear"}),
        method="GET",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    error = _expect_http_error(request, 409)
    payload = json.loads(error.read().decode("utf-8"))
    if payload.get("error") != "mutation_not_allowed":
        raise RuntimeError("public query did not enforce mutation ceiling")


def _verify_public_mcp(bearer: str) -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "watchtower-product-acceptance",
                "version": "1.0",
            },
        },
    }
    status, initialized, headers = _mcp_post(bearer, initialize)
    if status != 200:
        raise RuntimeError(f"MCP initialize returned {status}")
    result = initialized.get("result", {})
    protocol_version = result.get("protocolVersion")
    if not protocol_version:
        raise RuntimeError("MCP initialize did not negotiate a protocol version")
    session_id = headers.get("Mcp-Session-Id")

    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    status, _, _ = _mcp_post(
        bearer,
        notification,
        session_id=session_id,
        protocol_version=protocol_version,
    )
    if status not in {200, 202}:
        raise RuntimeError(f"MCP initialized notification returned {status}")

    status, listed, _ = _mcp_post(
        bearer,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id=session_id,
        protocol_version=protocol_version,
    )
    if status != 200:
        raise RuntimeError(f"MCP tools/list returned {status}")
    tools = {
        tool.get("name"): tool
        for tool in listed.get("result", {}).get("tools", [])
    }
    if set(tools) != {"gway", "query"}:
        raise RuntimeError(f"unexpected MCP tool set: {sorted(tools)}")
    annotations = tools["query"].get("annotations", {})
    if annotations.get("readOnlyHint") is not True:
        raise RuntimeError("MCP query tool is not advertised read-only")

    status, called, _ = _mcp_post(
        bearer,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "query",
                "arguments": {"command": "log sources"},
            },
        },
        session_id=session_id,
        protocol_version=protocol_version,
    )
    if status != 200:
        raise RuntimeError(f"MCP query call returned {status}")
    result = called.get("result", {})
    if result.get("isError") is True:
        raise RuntimeError("MCP query tool returned an error result")
    if not result.get("content"):
        raise RuntimeError("MCP query tool returned no content")


def _verify_authenticated_public_surfaces() -> None:
    scopes = ScopeRegistry(SECURITY_STATE)
    tokens = TokenRegistry(SECURITY_STATE)
    read_token_name = "watchtower-product-acceptance"
    mutate_scope_name = "watchtower-product-acceptance-mutate"
    mutate_token_name = "watchtower-product-acceptance-mutate"

    tokens.remove(read_token_name)
    tokens.remove(mutate_token_name)
    scopes.remove(mutate_scope_name)
    read_bearer = None
    mutate_bearer = None
    try:
        read_bearer = tokens.create(
            read_token_name,
            scopes={"chatgpt-logs"},
        ).bearer
        scopes.replace(
            mutate_scope_name,
            operations={"clear"},
            environment=(),
        )
        mutate_bearer = tokens.create(
            mutate_token_name,
            scopes={mutate_scope_name},
        ).bearer

        _verify_public_query(read_bearer)
        _verify_public_mutation_ceiling(mutate_bearer)
        _verify_public_mcp(read_bearer)
    finally:
        tokens.remove(read_token_name)
        tokens.remove(mutate_token_name)
        scopes.remove(mutate_scope_name)


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

    client = _json(ACCEPTANCE_CLIENT)
    if client.get("client_id") != ACCEPTANCE_CLIENT:
        raise RuntimeError("acceptance-client metadata has wrong client_id")
    if "http://127.0.0.1:8765/callback" not in client.get("redirect_uris", []):
        raise RuntimeError("acceptance-client metadata has wrong redirect URI")
    if "none" not in client.get("token_endpoint_auth_methods", []):
        raise RuntimeError("acceptance-client metadata is not a public OAuth client")

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

    _verify_authenticated_public_surfaces()


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
