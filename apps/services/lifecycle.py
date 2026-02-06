from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import LifecycleService


logger = logging.getLogger(__name__)

SERVICE_NAME_LOCK = "service.lck"
SYSTEMD_UNITS_LOCK = "systemd_services.lck"
LIFECYCLE_CONFIG = "lifecycle_services.json"


@dataclass(frozen=True)
class LifecycleConfig:
    services: list[dict[str, object]]
    systemd_units: list[str]
    service_name: str


def lock_dir(base_dir: Path | None = None) -> Path:
    """Return the lock directory for lifecycle service configuration."""
    base = Path(base_dir or settings.BASE_DIR)
    path = base / ".locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_service_name(lock_path: Path) -> str:
    """Read the configured service name from the lock file."""
    try:
        return lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _service_docs_url(doc: str) -> str:
    """Return the documentation URL for a given document slug."""
    if not doc:
        return ""
    try:
        return reverse("docs:docs-document", args=[doc])
    except NoReverseMatch:
        return ""


def _normalize_unit(unit_name: str) -> tuple[str, str]:
    """Normalize a unit name for systemd display and lookups."""
    normalized = unit_name.strip()
    unit_display = normalized
    unit = normalized
    if normalized.endswith(".service"):
        unit = normalized.removesuffix(".service")
    else:
        unit_display = f"{normalized}.service"
    return unit, unit_display


def _add_unit(
    service_units: list[dict[str, object]],
    unit_name: str,
    *,
    key: str | None = None,
    label: str | None = None,
    configured: bool = True,
    docs_url: str = "",
    pid_file: str = "",
) -> None:
    """Add or update a unit entry in the service list."""
    normalized = unit_name.strip()
    if not normalized or normalized.startswith("-"):
        return

    unit, unit_display = _normalize_unit(normalized)
    for existing_unit in service_units:
        if existing_unit["unit_display"] == unit_display:
            if key and not existing_unit.get("key"):
                existing_unit["key"] = key
            existing_unit["label"] = label or existing_unit["label"]
            existing_unit["configured"] = configured
            if docs_url:
                existing_unit["docs_url"] = docs_url
            if pid_file and not existing_unit.get("pid_file"):
                existing_unit["pid_file"] = pid_file
            return
    service_units.append(
        {
            "key": key or "",
            "label": label or normalized,
            "unit": unit,
            "unit_display": unit_display,
            "configured": configured,
            "docs_url": docs_url,
            "pid_file": pid_file or "",
        }
    )


def _read_extra_systemd_units(lock_path: Path) -> list[str]:
    """Return systemd units listed in the lock file."""
    try:
        return [line for line in lock_path.read_text(encoding="utf-8").splitlines() if line]
    except OSError:
        return []


def build_lifecycle_service_units(base_dir: Path | None = None) -> list[dict[str, object]]:
    """Build a list of configured lifecycle service units."""
    resolved_base = Path(base_dir or settings.BASE_DIR)
    locks = lock_dir(resolved_base)
    service_name = read_service_name(locks / SERVICE_NAME_LOCK)
    service_name_placeholder = service_name or "SERVICE_NAME"

    service_units: list[dict[str, object]] = []

    for service in LifecycleService.objects.all():
        unit_name = service.unit_template.format(service=service_name_placeholder)
        configured = service.is_configured(service_name=service_name, lock_dir=locks)
        _add_unit(
            service_units,
            unit_name,
            key=service.slug,
            label=service.display,
            configured=configured,
            docs_url=_service_docs_url(service.docs_path),
            pid_file=service.pid_file,
        )

    for unit_name in _read_extra_systemd_units(locks / SYSTEMD_UNITS_LOCK):
        _add_unit(
            service_units,
            unit_name,
            configured=True,
        )

    return service_units


def build_lifecycle_config(base_dir: Path | None = None) -> LifecycleConfig:
    """Build lifecycle configuration payloads for services and systemd."""
    resolved_base = Path(base_dir or settings.BASE_DIR)
    locks = lock_dir(resolved_base)
    service_name = read_service_name(locks / SERVICE_NAME_LOCK)
    service_units = build_lifecycle_service_units(resolved_base)
    systemd_units = [
        unit["unit_display"]
        for unit in service_units
        if unit.get("configured") and unit.get("unit_display")
    ]

    extras = [
        unit
        for unit in _read_extra_systemd_units(locks / SYSTEMD_UNITS_LOCK)
        if unit not in systemd_units
    ]
    systemd_units.extend(extras)

    return LifecycleConfig(
        services=service_units,
        systemd_units=systemd_units,
        service_name=service_name,
    )


def write_lifecycle_config(base_dir: Path | None = None) -> LifecycleConfig:
    """Write lifecycle configuration and lock files to disk."""
    resolved_base = Path(base_dir or settings.BASE_DIR)
    locks = lock_dir(resolved_base)
    config = build_lifecycle_config(resolved_base)

    payload = {
        "generated_at": timezone.now().isoformat(),
        "service_name": config.service_name,
        "services": config.services,
        "systemd_units": config.systemd_units,
    }
    config_path = locks / LIFECYCLE_CONFIG
    try:
        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Unable to write lifecycle services config", exc_info=True)

    lock_path = locks / SYSTEMD_UNITS_LOCK
    try:
        lock_path.write_text("\n".join(config.systemd_units) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Unable to write systemd services lock", exc_info=True)

    return config
