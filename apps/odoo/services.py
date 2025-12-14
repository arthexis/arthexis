from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.utils import timezone

from .models import OdooDeployment

CONFIG_ENV_VAR = "ODOO_RC"


@dataclass
class DiscoveredOdooConfig:
    path: Path
    options: dict[str, object]


class OdooConfigError(RuntimeError):
    """Raised when an odoo configuration file cannot be read."""


def _candidate_paths(additional_candidates: Iterable[Path | str] | None = None) -> list[Path]:
    env_path = os.environ.get(CONFIG_ENV_VAR) or ""
    home = Path.home()

    defaults: list[Path | str] = [
        env_path,
        "/etc/odoo/odoo.conf",
        "/etc/odoo.conf",
        home / ".odoorc",
        home / ".config/odoo/odoo.conf",
    ]

    candidates: list[Path] = []
    seen: set[Path] = set()

    for candidate in [*defaults, *(additional_candidates or [])]:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            candidates.append(path)
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
    parser = configparser.ConfigParser()
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
) -> tuple[list[DiscoveredOdooConfig], list[str]]:
    """Return discovered odoo configurations and any warnings."""

    discovered: list[DiscoveredOdooConfig] = []
    errors: list[str] = []

    for path in _candidate_paths(additional_candidates):
        try:
            options = _read_config(path)
        except OdooConfigError as exc:
            errors.append(str(exc))
            continue
        discovered.append(DiscoveredOdooConfig(path=path, options=options))

    return discovered, errors


def _deployment_defaults(entry: DiscoveredOdooConfig) -> dict[str, object]:
    options = entry.options

    http_port = _parse_int(
        options.get("http_port") or options.get("xmlrpc_port") or options.get("xmlrpcs_port")
    )

    defaults: dict[str, object] = {
        "name": _clean_text(options.get("instance_name")) or entry.path.stem,
        "config_path": str(entry.path),
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
) -> dict[str, object]:
    """Discover configurations and upsert :class:`OdooDeployment` entries."""

    discovered, errors = discover_odoo_configs(additional_candidates)

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
