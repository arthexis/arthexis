"""Local filesystem discovery helpers for nginx site configuration loading."""

from __future__ import annotations

from pathlib import Path

from apps.nginx import services

DEFAULT_SITE_DESTINATION = "/etc/nginx/sites-enabled/arthexis-sites.conf"
NGINX_PERMISSIONS_HELPER = "./scripts/nginx-perms.sh"


def _read_lock(lock_dir: Path, name: str, fallback: str) -> str:
    """Read a lock-file value, returning ``fallback`` when absent/unreadable/empty."""

    try:
        value = (lock_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    return value or fallback


def _read_int_lock(lock_dir: Path, name: str, fallback: int) -> int:
    """Read and validate a positive TCP port from lock-file text."""

    value = _read_lock(lock_dir, name, str(fallback))
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    if parsed < 1 or parsed > 65535:
        return fallback
    return parsed


def _discover_site_config_paths(site_path: Path | None) -> list[Path]:
    """Collect local nginx config paths from explicit, enabled, and fallback locations."""

    candidates: set[Path] = set()
    if site_path and site_path.exists():
        if site_path.is_dir():
            candidates.update(site_path.glob("arthexis*.conf"))
        else:
            candidates.add(site_path)

    enabled_candidates = [
        path
        for path in services.SITES_ENABLED_DIR.glob("arthexis*.conf")
        if not path.name.endswith("-sites.conf")
    ]
    candidates.update(enabled_candidates)
    if not enabled_candidates:
        available_candidates = [
            path
            for path in services.SITES_AVAILABLE_DIR.glob("arthexis*.conf")
            if not path.name.endswith("-sites.conf")
        ]
        candidates.update(available_candidates)
    return sorted(candidates)


def _resolve_site_destination() -> str:
    """Resolve the preferred managed site destination from local nginx directories."""

    candidates = [
        services.SITES_ENABLED_DIR / "arthexis-sites.conf",
        services.SITES_AVAILABLE_DIR / "arthexis-sites.conf",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return DEFAULT_SITE_DESTINATION


def _format_local_load_error(path: Path, exc: OSError) -> str:
    """Return a user-facing local-load error message with remediation hints."""

    message = f"{path}: {exc}"
    if isinstance(exc, PermissionError):
        return (
            f"{message} Run {NGINX_PERMISSIONS_HELPER} to grant read access "
            "to local nginx site files."
        )
    return message
