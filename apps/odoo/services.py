from __future__ import annotations

import configparser
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.utils import timezone

from .models import OdooDeployment

CONFIG_ENV_VAR = "ODOO_RC"


pwd_spec = importlib.util.find_spec("pwd")
if pwd_spec:
    import pwd  # type: ignore
else:  # pragma: no cover - Windows and other platforms without ``pwd``
    pwd = None  # type: ignore


@dataclass
class DiscoveredOdooConfig:
    path: Path
    base_path: Path
    options: dict[str, object]


class OdooConfigError(RuntimeError):
    """Raised when an odoo configuration file cannot be read."""


def _get_uid() -> int:
    """Return the current user's UID, with a fallback for Windows."""

    return getattr(os, "getuid", lambda: 0)()


def _walk_for_configs(root: Path) -> Iterable[Path]:
    """Yield every ``odoo.conf`` under ``root`` while ignoring permission issues."""

    if not root.exists():
        return []

    if root.is_file():
        return [root] if root.name == "odoo.conf" else []

    found: list[Path] = []

    root = root.resolve()
    skip_roots = {
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
    }

    for current_root, dirs, files in os.walk(
        root, topdown=True, followlinks=False, onerror=lambda _: None
    ):
        current_root_path = Path(current_root)
        if current_root_path in skip_roots:
            dirs[:] = []
            continue
        dirs[:] = [
            directory
            for directory in dirs
            if (current_root_path / directory).resolve() not in skip_roots
        ]
        for file_name in files:
            if file_name == "odoo.conf":
                found.append(Path(current_root) / file_name)

    return found


def _default_config_locations() -> list[Path]:
    """Return standard config locations for the current OS user.

    This includes the process home directory (``Path.home()``) and the corresponding
    ``/home/<user>`` paths for the logged-in user (when different), mirroring where
    Odoo typically stores configuration files without scanning unrelated home trees.
    """

    home = Path.home()

    candidates: list[Path] = [home / ".odoorc", home / ".config/odoo/odoo.conf", home]

    user_home = None
    if pwd is not None:
        try:
            user_home = Path("/home") / pwd.getpwuid(_get_uid()).pw_name
        except Exception:  # pragma: no cover - fallback for platforms without ``pwd``
            user_home = None

    if user_home and user_home != home:
        candidates.extend(
            [user_home / ".odoorc", user_home / ".config/odoo/odoo.conf", user_home]
        )

    return candidates


def _candidate_paths(
    additional_candidates: Iterable[Path | str] | None = None, *, scan_filesystem: bool = True
) -> list[Path]:
    defaults: list[Path | str] = []
    if additional_candidates is None:
        env_path = os.environ.get(CONFIG_ENV_VAR) or ""
        defaults = [env_path, *_default_config_locations()]

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_path(path: Path):
        normalized = path.expanduser()
        if normalized in seen:
            return
        seen.add(normalized)
        if normalized.is_file():
            candidates.append(normalized)

    if scan_filesystem:
        for discovered in _walk_for_configs(Path("/")):
            add_path(discovered)

    for candidate in defaults:
        if candidate:
            candidate_path = Path(candidate)
            if candidate_path.is_dir():
                for discovered in _walk_for_configs(candidate_path):
                    add_path(discovered)
            else:
                add_path(candidate_path)

    for candidate in additional_candidates or []:
        candidate_path = Path(candidate)
        if candidate_path.is_dir():
            for discovered in _walk_for_configs(candidate_path):
                add_path(discovered)
        else:
            add_path(candidate_path)

    return candidates


def _parse_int(value: object) -> int | None:
    if value in (None, "", "False", "false"):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _clean_text(value: object) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if text.lower() == "false":
        return ""
    return text


def _read_config(path: Path) -> dict[str, object]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except Exception as exc:  # pragma: no cover - filesystem errors
        raise OdooConfigError(f"Unable to read {path}: {exc}") from exc

    if parser.has_section("options"):
        options = parser["options"]
    else:
        options = parser.defaults()

    if not options:
        raise OdooConfigError("Missing [options] section")

    return {key: value for key, value in options.items()}


def discover_odoo_configs(
    additional_candidates: Iterable[Path | str] | None = None,
    *,
    scan_filesystem: bool = True,
) -> tuple[list[DiscoveredOdooConfig], list[str]]:
    """Return discovered odoo configurations and any warnings."""

    discovered: list[DiscoveredOdooConfig] = []
    errors: list[str] = []

    for path in _candidate_paths(additional_candidates, scan_filesystem=scan_filesystem):
        try:
            options = _read_config(path)
        except OdooConfigError as exc:
            errors.append(str(exc))
            continue
        discovered.append(
            DiscoveredOdooConfig(path=path, base_path=path.parent, options=options)
        )

    return discovered, errors


def _deployment_defaults(entry: DiscoveredOdooConfig) -> dict[str, object]:
    options = entry.options

    http_port = _parse_int(
        options.get("http_port") or options.get("xmlrpc_port") or options.get("xmlrpcs_port")
    )

    defaults: dict[str, object] = {
        "name": _clean_text(options.get("instance_name")) or entry.path.stem,
        "config_path": str(entry.path),
        "base_path": str(entry.base_path),
        "addons_path": _clean_text(options.get("addons_path")),
        "data_dir": _clean_text(options.get("data_dir")),
        "db_host": _clean_text(options.get("db_host")),
        "db_port": _parse_int(options.get("db_port")),
        "db_user": _clean_text(options.get("db_user")),
        "db_password": _clean_text(options.get("db_password")),
        "db_name": _clean_text(options.get("db_name")),
        "db_filter": _clean_text(options.get("dbfilter")),
        "admin_password": _clean_text(options.get("admin_passwd")),
        "http_port": http_port,
        "longpolling_port": _parse_int(options.get("longpolling_port")),
        "logfile": _clean_text(options.get("logfile")),
        "last_discovered": timezone.now(),
    }

    return defaults


def sync_odoo_deployments(
    additional_candidates: Iterable[Path | str] | None = None,
    *,
    scan_filesystem: bool = True,
) -> dict[str, object]:
    """Discover configurations and upsert :class:`OdooDeployment` entries."""

    discovered, errors = discover_odoo_configs(
        additional_candidates, scan_filesystem=scan_filesystem
    )

    created = 0
    updated = 0
    instances: list[OdooDeployment] = []

    for entry in discovered:
        defaults = _deployment_defaults(entry)
        obj, created_flag = OdooDeployment.objects.update_or_create(
            config_path=defaults["config_path"], defaults=defaults
        )
        instances.append(obj)
        if created_flag:
            created += 1
        else:
            updated += 1

    return {
        "instances": instances,
        "created": created,
        "updated": updated,
        "found": len(discovered),
        "errors": errors,
    }
