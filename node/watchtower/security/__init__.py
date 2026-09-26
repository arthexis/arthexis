"""Read-only Watchtower security inspection operations."""

from pathlib import Path
import socket
import stat

SECURITY_STATE = Path("/var/lib/gway/cache/security/state.sqlite")

def _listener(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False

def _check(name: str, ok: bool, detail: object = None) -> dict:
    result = {"name": name, "ok": bool(ok)}
    if detail is not None:
        result["detail"] = detail
    return result

def audit(
    host: str = "127.0.0.1",
    mcp_port: int = 8000,
    oauth_port: int = 8001,
    security_state: str = str(SECURITY_STATE),
    *,
    mutate: bool = False,
) -> dict:
    """Audit the local Watchtower remote-access boundary without mutation."""
    del mutate
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    checks = [
        _check("mcp_loopback", loopback, host),
        _check("oauth_loopback", loopback, host),
        _check("mcp_listener", _listener(host, mcp_port), f"{host}:{mcp_port}"),
        _check("oauth_listener", _listener(host, oauth_port), f"{host}:{oauth_port}"),
    ]
    path = Path(security_state)
    exists = path.is_file()
    checks.append(_check("security_state_exists", exists, str(path)))
    if exists:
        mode = stat.S_IMODE(path.stat().st_mode)
        unsafe = bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
        checks.append(_check("security_state_not_group_world_writable", not unsafe, oct(mode)))
    return {
        "role": "watchtower",
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }
