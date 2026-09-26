"""Project-owned Watchtower operations exposed through GWAY's node family."""

from __future__ import annotations

from pathlib import Path
import os
import socket
import stat

from arthexis.ready import local as application_ready


SECURITY_STATE = Path("/var/lib/gway/cache/security/state.sqlite")
ROLE_FILE = Path("/var/lib/arthexis/role")


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


def status(
    host: str = "127.0.0.1",
    app_port: int = 8888,
    mcp_port: int = 8000,
    oauth_port: int = 8001,
    domain: str = "arthexis.com",
    *,
    mutate: bool = False,
) -> dict:
    """Return the local Watchtower application and remote-service status."""
    del mutate
    checks = [
        _check(
            "application",
            application_ready(host=host, port=app_port, domain=domain),
            f"{host}:{app_port}",
        ),
        _check("mcp", _listener(host, mcp_port), f"{host}:{mcp_port}"),
        _check("oauth", _listener(host, oauth_port), f"{host}:{oauth_port}"),
    ]
    return {
        "role": "watchtower",
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }


def diagnose(
    host: str = "127.0.0.1",
    app_port: int = 8888,
    mcp_port: int = 8000,
    oauth_port: int = 8001,
    domain: str = "arthexis.com",
    *,
    mutate: bool = False,
) -> dict:
    """Run the maintained local Watchtower diagnostic aggregation."""
    del mutate
    node_status = status(
        host=host,
        app_port=app_port,
        mcp_port=mcp_port,
        oauth_port=oauth_port,
        domain=domain,
    )

    django_ok = True
    django_detail = "system checks passed"
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")
        import django
        from django.core.management import call_command

        django.setup()
        call_command("check", fail_level="ERROR", verbosity=0)
    except Exception as exc:
        django_ok = False
        django_detail = f"{type(exc).__name__}: {exc}"

    role_ok = False
    role_detail = "role file missing"
    try:
        role_value = ROLE_FILE.read_text(encoding="utf-8").strip()
        role_ok = role_value.casefold() == "watchtower"
        role_detail = role_value or "empty role"
    except OSError:
        pass

    checks = [
        *node_status["checks"],
        _check("django", django_ok, django_detail),
        _check("role", role_ok, role_detail),
    ]
    return {
        "role": "watchtower",
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }


def security_audit(
    host: str = "127.0.0.1",
    mcp_port: int = 8000,
    oauth_port: int = 8001,
    security_state: str = str(SECURITY_STATE),
    *,
    mutate: bool = False,
) -> dict:
    """Audit the local Watchtower remote-access boundary without mutation."""
    del mutate
    checks = [
        _check("mcp_loopback", host in {"127.0.0.1", "::1", "localhost"}, host),
        _check("oauth_loopback", host in {"127.0.0.1", "::1", "localhost"}, host),
        _check("mcp_listener", _listener(host, mcp_port), f"{host}:{mcp_port}"),
        _check("oauth_listener", _listener(host, oauth_port), f"{host}:{oauth_port}"),
    ]

    path = Path(security_state)
    exists = path.is_file()
    checks.append(_check("security_state_exists", exists, str(path)))
    if exists:
        mode = stat.S_IMODE(path.stat().st_mode)
        unsafe = bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
        checks.append(
            _check(
                "security_state_not_group_world_writable",
                not unsafe,
                oct(mode),
            )
        )

    return {
        "role": "watchtower",
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }
