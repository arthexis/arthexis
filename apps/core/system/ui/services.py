from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.systemctl import _systemctl_command
from apps.screens.startup_notifications import lcd_feature_enabled
from apps.cards.rfid_service import rfid_service_enabled

from ..filesystem import _pid_file_running, _read_service_mode
from .runtime import _systemd_unit_status


SERVICE_REPORT_DEFINITIONS = (
    {
        "key": "suite",
        "label": _("Suite service"),
        "unit_template": "{service}.service",
        "pid_file": "django.pid",
        "docs": "services/suite-service.md",
    },
    {
        "key": "celery-worker",
        "label": _("Celery worker"),
        "unit_template": "celery-{service}.service",
        "pid_file": "celery_worker.pid",
        "docs": "services/celery-worker.md",
    },
    {
        "key": "celery-beat",
        "label": _("Celery beat"),
        "unit_template": "celery-beat-{service}.service",
        "pid_file": "celery_beat.pid",
        "docs": "services/celery-beat.md",
    },
    {
        "key": "lcd-screen",
        "label": _("LCD screen"),
        "unit_template": "lcd-{service}.service",
        "pid_file": "lcd.pid",
        "docs": "services/lcd-screen.md",
    },
    {
        "key": "rfid-service",
        "label": _("RFID scanner service"),
        "unit_template": "rfid-{service}.service",
        "docs": "services/rfid-scanner-service.md",
    },
)


def _service_docs_url(doc: str) -> str:
    """Return the documentation URL for a service."""

    try:
        return reverse("docs:docs-document", args=[doc])
    except NoReverseMatch:
        return ""


def _configured_service_units(base_dir: Path) -> list[dict[str, object]]:
    """Return service units configured for this instance."""

    from apps.celery.utils import is_celery_enabled

    lock_dir = base_dir / ".locks"
    service_file = lock_dir / "service.lck"
    systemd_services_file = lock_dir / "systemd_services.lck"

    try:
        service_name = service_file.read_text(encoding="utf-8").strip()
    except OSError:
        service_name = ""

    try:
        systemd_units = systemd_services_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        systemd_units = []

    service_units: list[dict[str, object]] = []

    def _normalize_unit(unit_name: str) -> tuple[str, str]:
        """Normalize a unit name to (unit, display) values."""

        normalized = unit_name.strip()
        unit_display = normalized
        unit = normalized
        if normalized.endswith(".service"):
            unit = normalized.removesuffix(".service")
        else:
            unit_display = f"{normalized}.service"
        return unit, unit_display

    def _add_unit(
        unit_name: str,
        *,
        key: str | None = None,
        label: str | None = None,
        configured: bool = True,
        docs_url: str = "",
        pid_file: str = "",
    ) -> None:
        """Add or update an entry in the service list."""

        normalized = unit_name.strip()
        if not normalized:
            return

        unit, unit_display = _normalize_unit(normalized)
        for existing_unit in service_units:
            if existing_unit["unit_display"] == unit_display:
                if key and not existing_unit.get("key"):
                    existing_unit["key"] = key
                existing_unit["label"] = label or existing_unit["label"]
                existing_unit["configured"] = configured
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

    service_name_placeholder = service_name or "SERVICE_NAME"
    celery_enabled = is_celery_enabled(lock_dir / "celery.lck")
    lcd_enabled = lcd_feature_enabled(lock_dir)
    rfid_enabled = rfid_service_enabled(lock_dir)
    feature_map = {
        "celery-worker": celery_enabled,
        "celery-beat": celery_enabled,
        "lcd-screen": lcd_enabled,
        "rfid-service": rfid_enabled,
    }

    for spec in SERVICE_REPORT_DEFINITIONS:
        unit_name = spec["unit_template"].format(service=service_name_placeholder)
        if not service_name:
            configured = False
        elif spec["key"] == "suite":
            configured = True
        else:
            configured = feature_map.get(spec["key"], False)

        _add_unit(
            unit_name,
            key=spec.get("key"),
            label=str(spec["label"]),
            configured=configured,
            docs_url=_service_docs_url(spec["docs"]),
            pid_file=spec.get("pid_file", ""),
        )

    base_label_map: dict[str, str] = {}
    docs_url_map: dict[str, str] = {}
    if service_name:
        base_label_map = {
            f"{service_name}.service": str(_("Suite service")),
            f"celery-{service_name}.service": str(_("Celery worker")),
            f"celery-beat-{service_name}.service": str(_("Celery beat")),
            f"lcd-{service_name}.service": str(_("LCD screen")),
            f"rfid-{service_name}.service": str(_("RFID scanner service")),
        }
        for spec in SERVICE_REPORT_DEFINITIONS:
            docs_url_map[
                spec["unit_template"].format(service=service_name)
            ] = _service_docs_url(spec["docs"])

    for unit_name in systemd_units:
        normalized = unit_name.strip()
        _add_unit(
            normalized,
            label=base_label_map.get(normalized),
            configured=True,
            docs_url=docs_url_map.get(normalized, ""),
        )

    return service_units


def _embedded_service_status(lock_dir: Path, pid_file: str) -> dict[str, object]:
    """Return status information for embedded services."""

    running = _pid_file_running(lock_dir / pid_file)
    status_label = _("active (embedded)") if running else _("inactive (embedded)")
    return {
        "status": str(status_label),
        "enabled": str(_("Embedded")),
        "missing": False,
    }


def _build_services_report() -> dict[str, object]:
    """Return status data for system services."""

    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    configured_units = _configured_service_units(base_dir)
    command = _systemctl_command()
    service_mode = _read_service_mode(lock_dir)
    embedded_mode = service_mode == "embedded"

    services: list[dict[str, object]] = []
    for unit in configured_units:
        if unit.get("configured"):
            pid_file = unit.get("pid_file", "")
            if embedded_mode and pid_file:
                status_info = _embedded_service_status(lock_dir, pid_file)
            else:
                status_info = _systemd_unit_status(unit["unit"], command=command)
        else:
            status_info = {
                "status": str(_("Not configured")),
                "enabled": "",
                "missing": False,
            }
        services.append({**unit, **status_info})

    return {
        "services": services,
        "systemd_available": bool(command),
        "has_services": bool(configured_units),
    }
