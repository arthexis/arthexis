from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.translation import gettext as _

from ocpp import store
from ocpp.models import Charger, Transaction
from ocpp.status_display import ERROR_OK_VALUES, STATUS_BADGE_MAP


def _has_active_session(tx_obj) -> bool:
    """Return whether the provided transaction-like object is active."""

    if isinstance(tx_obj, (list, tuple, set)):
        return any(_has_active_session(item) for item in tx_obj)
    if not tx_obj:
        return False
    if isinstance(tx_obj, dict):
        return tx_obj.get("stop_time") is None
    stop_time = getattr(tx_obj, "stop_time", None)
    return stop_time is None


def _active_transaction(charger: Charger) -> Transaction | dict | None:
    """Return an in-memory or persisted transaction for the charger."""

    tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
    if tx_obj:
        return tx_obj
    return (
        Transaction.objects.filter(charger=charger, stop_time__isnull=True)
        .order_by("-start_time")
        .first()
    )


def _connector_state(charger: Charger) -> tuple[str, str, bool]:
    """Return label, color, and whether the connector is currently charging."""

    tx_obj = _active_transaction(charger)
    has_session = _has_active_session(tx_obj)

    status_value = (charger.last_status or "").strip()
    normalized_status = status_value.casefold() if status_value else ""
    label, color = STATUS_BADGE_MAP.get(normalized_status, (status_value or _("Unknown"), "#6c757d"))

    error_code = (charger.last_error_code or "").strip()
    error_code_lower = error_code.lower()
    if (
        has_session
        and error_code_lower in ERROR_OK_VALUES
        and (normalized_status not in STATUS_BADGE_MAP or normalized_status == "available")
    ):
        label, color = STATUS_BADGE_MAP.get("charging", (_("Charging"), "#198754"))
    elif (
        not has_session
        and normalized_status in {"charging", "finishing"}
        and error_code_lower in ERROR_OK_VALUES
    ):
        label, color = STATUS_BADGE_MAP.get("available", (_("Available"), "#0d6efd"))
    elif error_code and error_code_lower not in ERROR_OK_VALUES:
        label = _("%(status)s (%(error)s)") % {"status": label, "error": error_code}
        color = "#dc3545"

    return str(label), color, has_session


def _normalize_output_dir(value: str | None) -> tuple[Path, bool]:
    """Return the output directory and whether the directory is temporary."""

    if value:
        output_dir = Path(value).expanduser().resolve()
        if output_dir.exists() and any(output_dir.iterdir()):
            raise CommandError(
                "--output-dir must be empty so the Pyxel viewport can be staged safely."
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, False

    temp_dir = Path(tempfile.mkdtemp(prefix="pyxel_viewport_"))
    return temp_dir, True


def _copy_project_assets(target_dir: Path) -> None:
    """Copy the Pyxel viewport assets into ``target_dir``."""

    project_root = Path(__file__).resolve().parents[2] / "pyxel_viewport"
    if not project_root.exists():
        raise CommandError(
            "Pyxel viewport assets are missing. Expected to find ocpp/pyxel_viewport."
        )
    shutil.copytree(project_root, target_dir, dirs_exist_ok=True)


def _connector_snapshot(connectors: Iterable[Charger]) -> dict:
    """Return a JSON-serializable payload describing connector state."""

    payload = {
        "generated_at": timezone.now().isoformat(),
        "connectors": [],
    }
    for connector in connectors:
        status_label, status_color, is_charging = _connector_state(connector)
        payload["connectors"].append(
            {
                "serial": connector.charger_id,
                "connector_id": connector.connector_id,
                "connector_label": str(connector.connector_label),
                "display_name": connector.display_name or "",
                "status_label": status_label,
                "status_color": status_color,
                "is_charging": is_charging,
                "location": str(connector.location) if connector.location_id else "",
            }
        )
    return payload


def _write_snapshot(target_dir: Path, snapshot: dict) -> Path:
    """Persist the snapshot to ``data/connectors.json`` and return the path."""

    data_dir = target_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "connectors.json"
    json_path.write_text(json.dumps(snapshot, indent=2))
    return json_path


def _resolve_pyxel_runner(runner: str) -> list[str]:
    """Return a usable Pyxel runner command or raise when it cannot be found."""

    parts = shlex.split(runner)
    if not parts:
        raise CommandError(
            "Pyxel runner not found. Install Pyxel or provide the path with --pyxel-runner."
        )

    executable = Path(parts[0]).expanduser()
    if executable.exists():
        parts[0] = str(executable)
        return parts

    discovered = shutil.which(parts[0])
    if discovered:
        parts[0] = discovered
        return parts

    raise CommandError(
        "Pyxel runner not found. Install Pyxel or provide the path with --pyxel-runner."
    )


def _launch_pyxel(runner_parts: list[str], runtime_dir: Path) -> None:
    """Start the Pyxel viewport using the provided runtime directory."""

    command = runner_parts + ["run", "main.py"]
    subprocess.run(command, cwd=runtime_dir, check=True)


class Command(BaseCommand):
    help = (
        "Prepare connector data and launch a Pyxel viewport that renders a "
        "charging animation for each configured connector."
    )

    def add_arguments(self, parser) -> None:  # pragma: no cover - simple wiring
        parser.add_argument(
            "--output-dir",
            dest="output_dir",
            help=(
                "Directory used to stage the Pyxel project. Defaults to a temporary "
                "folder that is cleaned up after the viewport exits."
            ),
        )
        parser.add_argument(
            "--pyxel-runner",
            dest="pyxel_runner",
            default="pyxel",
            help="Pyxel executable or command to invoke when launching the viewport.",
        )
        parser.add_argument(
            "--skip-launch",
            action="store_true",
            help=(
                "Prepare the Pyxel project without launching the window. Use this "
                "flag in CI environments or when you only need the staged assets."
            ),
        )

    def handle(self, *args, **options):
        output_dir_option = options.get("output_dir")
        pyxel_runner = options.get("pyxel_runner")
        skip_launch = options.get("skip_launch")

        connectors = (
            Charger.objects.exclude(connector_id__isnull=True)
            .order_by("charger_id", "connector_id")
            .select_related("location")
        )
        snapshot = _connector_snapshot(connectors)
        if not snapshot["connectors"]:
            self.stdout.write(
                self.style.WARNING(
                    "No connectors with a connector id were found. The viewport will start with an empty list."
                )
            )

        runtime_dir, is_temp_dir = _normalize_output_dir(output_dir_option)
        _copy_project_assets(runtime_dir)
        snapshot_path = _write_snapshot(runtime_dir, snapshot)
        self.stdout.write(self.style.SUCCESS(f"Connector snapshot written to {snapshot_path}"))

        if skip_launch:
            self.stdout.write(
                "Skipping Pyxel launch; run `pyxel run main.py` from %s to open the viewport." % runtime_dir
            )
            return

        cleanup_required = is_temp_dir
        try:
            runner_parts = _resolve_pyxel_runner(pyxel_runner)
            _launch_pyxel(runner_parts, runtime_dir)
        finally:
            if cleanup_required:
                shutil.rmtree(runtime_dir, ignore_errors=True)
