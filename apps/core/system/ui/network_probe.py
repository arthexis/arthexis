"""Network and process probing utilities used by system UI reports.

Data flow:
- Determine whether the suite is reachable by first checking for a running
  ``manage.py runserver`` process and extracting its port from command output.
- Probe a prioritized set of localhost ports when process inspection is
  inconclusive.
- Build nginx expected/actual comparison payloads used by the admin report.

Parsed command formats:
- ``pgrep -af manage.py runserver`` lines (free-form command strings).
- nginx site file content loaded as UTF-8 text and normalized for comparison.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.nginx.renderers import generate_primary_config

from utils.service_probe import detect_runserver_port, probe_admin_login

from ..filesystem import _configured_backend_port, _nginx_site_path, _resolve_nginx_mode

logger = logging.getLogger(__name__)


def _normalize_nginx_content(content: str) -> str:
    """Return *content* with trailing newlines removed for comparison."""

    return content.rstrip("\n")


def _resolve_external_websockets(default: bool = True) -> bool:
    """Read external websocket mode from enabled nginx site config if present."""

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
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unable to generate expected nginx configuration")
        expected_error = str(exc)

    actual_content = ""
    actual_error = ""
    try:
        raw_content = resolved_site_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        actual_error = _("NGINX configuration file not found.")
    except OSError as exc:  # pragma: no cover - unexpected filesystem error
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

    port = detect_runserver_port()
    if port is None:
        return True, _configured_backend_port(Path(settings.BASE_DIR))
    return True, port


def _probe_ports(candidates: list[int]) -> tuple[bool, int | None]:
    """Attempt to probe the admin login endpoint on the provided ports."""

    for port in candidates:
        result = probe_admin_login(port, timeout=0.25)
        if result.reachable:
            return True, port
    return False, None


def _port_candidates(default_port: int) -> list[int]:
    """Return a prioritized list of ports to probe for the HTTP service."""

    candidates = [default_port] if 1 <= default_port <= 65535 else []
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates
