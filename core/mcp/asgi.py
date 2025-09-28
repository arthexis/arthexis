"""ASGI integration helpers for exposing the MCP sigil resolver."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Awaitable, Callable, Tuple

from asgiref.typing import ASGI3Application, Scope
from django.conf import settings

from .server import SigilResolverServer, _normalize_mount_path

DEFAULT_MOUNT_PATH = "/mcp"


def _raw_config() -> dict[str, Any]:
    return dict(getattr(settings, "MCP_SIGIL_SERVER", {}))


def configured_mount_path() -> str:
    """Return the configured mount path for the embedded MCP server."""

    return _normalize_mount_path(_raw_config().get("mount_path"), default=DEFAULT_MOUNT_PATH)


@lru_cache(maxsize=1)
def _load_app() -> Tuple[str, ASGI3Application, SigilResolverServer]:
    """Build the FastMCP SSE application once and reuse it."""

    config = _raw_config()
    mount_path = configured_mount_path()
    config["mount_path"] = mount_path
    server = SigilResolverServer(config)
    fastmcp = server.build_fastmcp()
    app = fastmcp.sse_app(mount_path=mount_path)
    return mount_path, app, server


def is_mcp_scope(scope: Scope) -> bool:
    """Return ``True`` when the scope targets the embedded MCP server."""

    path = scope.get("path") or ""
    mount_path = configured_mount_path()
    if mount_path == "/":
        return True
    return path == mount_path or path.startswith(f"{mount_path}/")


ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


async def application(scope: Scope, receive: ASGIReceive, send: ASGISend) -> None:
    """ASGI entrypoint forwarding HTTP traffic to the FastMCP app."""

    mount_path, app, _ = _load_app()
    path = scope.get("path") or ""

    if mount_path != "/" and path.startswith(mount_path):
        trimmed = path[len(mount_path) :]
        if not trimmed:
            trimmed = "/"
        elif not trimmed.startswith("/"):
            trimmed = f"/{trimmed}"
    else:
        trimmed = path or "/"

    inner_scope = dict(scope)
    inner_scope["path"] = trimmed
    inner_scope["raw_path"] = trimmed.encode("utf-8")

    await app(inner_scope, receive, send)


def reset_cache() -> None:
    """Reset cached application instances (used in tests)."""

    _load_app.cache_clear()
