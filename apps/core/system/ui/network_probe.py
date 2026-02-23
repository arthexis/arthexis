"""Network/process probing and nginx comparison helpers for the system UI.

Data flow: callers provide optional filesystem paths and configuration flags;
this module inspects process listings, TCP sockets, and nginx config contents
to return normalized dictionaries consumed by UI/report layers.

Expected input formats: runserver process lines are raw ``pgrep -af`` strings,
and nginx files are plain UTF-8 text rendered by the managed template.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import logging
import re
import socket
import subprocess

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.nginx.renderers import generate_primary_config

from ..filesystem import _configured_backend_port, _nginx_site_path, _resolve_nginx_mode

logger = logging.getLogger(__name__)

_RUNSERVER_PORT_PATTERN = re.compile(r":(\d{2,5})(?:\D|$)")
_RUNSERVER_PORT_FLAG_PATTERN = re.compile(r"--port(?:=|\s+)(\d{2,5})", re.IGNORECASE)


def _parse_runserver_port(command_line: str) -> int | None:
    """Extract the HTTP port from a runserver command line."""

    for pattern in (_RUNSERVER_PORT_PATTERN, _RUNSERVER_PORT_FLAG_PATTERN):
        match = pattern.search(command_line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _normalize_nginx_content(content: str) -> str:
    """Return *content* with trailing newlines removed for comparison."""

    return content.rstrip("\n")


def _resolve_external_websockets(default: bool = True) -> bool:
    """Read external websocket mode from enabled SiteConfiguration if present."""

    try:
        from apps.nginx.models import SiteConfiguration

        config = SiteConfiguration.objects.filter(enabled=True).order_by("pk").first()
        if config is not None:
            return bool(config.external_websockets)
    except Exception:
        return default
    return default


def _build_nginx_report(
    *,
    base_dir: Path | None = None,
    site_path: Path | None = None,
    external_websockets: bool | None = None,
) -> dict[str, object]:
    """Return comparison data for the managed nginx configuration file."""

    resolved_base = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
    resolved_site_path = Path(site_path) if site_path is not None else _nginx_site_path()

    mode = _resolve_nginx_mode(resolved_base)
    port = _configured_backend_port(resolved_base)

    expected_content = ""
    expected_error = ""
    resolved_websockets = (
        _resolve_external_websockets()
        if external_websockets is None
        else external_websockets
    )
    try:
        expected_content = _normalize_nginx_content(
            generate_primary_config(mode, port, external_websockets=resolved_websockets)
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("Unable to generate expected nginx configuration")
        expected_error = str(exc)

    actual_content = ""
    actual_error = ""
    try:
        raw_content = resolved_site_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        actual_error = _("NGINX configuration file not found.")
    except OSError as exc:  # pragma: no cover
        actual_error = str(exc)
    else:
        actual_content = _normalize_nginx_content(raw_content)

    differs = bool(expected_error or actual_error or expected_content != actual_content)

    return {
        "expected_path": resolved_site_path,
        "actual_path": resolved_site_path,
        "expected_content": expected_content,
        "expected_error": expected_error,
        "actual_content": actual_content,
        "actual_error": actual_error,
        "differs": differs,
        "mode": mode,
        "port": port,
        "external_websockets": resolved_websockets,
    }


def _detect_runserver_process() -> tuple[bool, int | None]:
    """Return whether the dev server is running and the port if available."""

    try:
        result = subprocess.run(
            ["pgrep", "-af", "manage.py runserver"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, None
    except Exception:
        return False, None

    if result.returncode != 0:
        return False, None

    output = result.stdout.strip()
    if not output:
        return False, None

    port = None
    for line in output.splitlines():
        port = _parse_runserver_port(line)
        if port is not None:
            break

    if port is None:
        port = _configured_backend_port(Path(settings.BASE_DIR))

    return True, port


def _probe_ports(candidates: list[int]) -> tuple[bool, int | None]:
    """Attempt to connect to localhost on the provided ports."""

    for port in candidates:
        try:
            with closing(socket.create_connection(("localhost", port), timeout=0.25)):
                return True, port
        except OSError:
            continue
    return False, None


def _port_candidates(default_port: int) -> list[int]:
    """Return a prioritized list of ports to probe for the HTTP service."""

    candidates = [default_port]
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates
