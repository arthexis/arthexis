from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
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


_OUTPUT_DIR_MARKER = ".pyxel_viewport"


def _normalize_output_dir(value: str | None) -> tuple[Path, bool]:
    """Return the output directory and whether the directory is temporary."""

    def _write_marker(path: Path) -> None:
        marker_path = path / _OUTPUT_DIR_MARKER
        marker_path.write_text("Pyxel viewport output directory; safe to clear.")

    if value:
        output_dir = Path(value).expanduser().resolve()
        if output_dir.exists():
            if not output_dir.is_dir():
                raise CommandError("--output-dir must be a directory.")
            marker_path = output_dir / _OUTPUT_DIR_MARKER
            has_marker = marker_path.exists()
            children = [child for child in output_dir.iterdir() if child.name != _OUTPUT_DIR_MARKER]
            if children and not has_marker:
                raise CommandError(
                    "--output-dir must be empty or previously initialized by pyxel_viewport."
                )
            for child in children:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_marker(output_dir)
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


def _connector_snapshot(connectors: Iterable[Charger], *, instance_running: bool) -> dict:
    """Return a JSON-serializable payload describing connector state."""

    payload = {
        "generated_at": timezone.now().isoformat(),
        "instance_running": bool(instance_running),
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


def _atomic_write_json(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2))
    temp_path.replace(target)
    return target


def _write_snapshot(target_dir: Path, snapshot: dict) -> Path:
    """Persist the snapshot to ``data/connectors.json`` and return the path."""

    data_dir = target_dir / "data"
    json_path = data_dir / "connectors.json"
    return _atomic_write_json(json_path, snapshot)


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


def _configured_backend_port(base_dir: Path) -> int:
    lock_file = base_dir / "locks" / "backend_port.lck"
    try:
        value = int(lock_file.read_text().strip())
    except (OSError, TypeError, ValueError):
        return 8888
    if 1 <= value <= 65535:
        return value
    return 8888


def _port_candidates(default_port: int) -> list[int]:
    candidates = [default_port]
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates


def _probe_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _detect_instance(base_dir: Path) -> tuple[bool, int]:
    default_port = _configured_backend_port(base_dir)
    for port in _port_candidates(default_port):
        if _probe_port(port):
            return True, port
    return False, default_port


def _start_instance(base_dir: Path, port: int, stdout=None) -> subprocess.Popen[bytes]:
    python_path = base_dir / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    if not python_path.exists():
        raise CommandError(
            "Virtual environment not found. Run install.sh or install.bat first."
        )

    command = [str(python_path), "manage.py", "runserver", f"0.0.0.0:{port}", "--noreload"]
    return subprocess.Popen(command, cwd=base_dir, stdout=stdout, stderr=stdout)


def _refresh_snapshot_periodically(
    runtime_dir: Path,
    stop_event: threading.Event,
    instance_state: dict[str, object],
    stdout,
    interval: float = 3.0,
) -> None:
    while not stop_event.wait(interval):
        try:
            close_old_connections()
            connectors = (
                Charger.objects.exclude(connector_id__isnull=True)
                .order_by("charger_id", "connector_id")
                .select_related("location")
            )
            instance_running = bool(instance_state.get("running"))
            port = instance_state.get("port")
            if not instance_running and isinstance(port, int):
                instance_running = _probe_port(port)
                instance_state["running"] = instance_running
            snapshot = _connector_snapshot(connectors, instance_running=instance_running)
            _write_snapshot(runtime_dir, snapshot)
        except Exception as exc:  # pragma: no cover - defensive logging
            stdout.write(f"Snapshot refresh failed: {exc}")
        finally:
            close_old_connections()


def _start_default_simulator() -> tuple[bool, str]:
    from ocpp.models import Simulator
    from ocpp.simulator import ChargePointSimulator

    default_simulator = (
        Simulator.objects.filter(default=True, is_deleted=False).order_by("pk").first()
    )
    if default_simulator is None:
        return False, "No default simulator configured."

    if default_simulator.pk in store.simulators:
        return False, "Default simulator is already running."

    store.register_log_name(default_simulator.cp_path, default_simulator.name, log_type="simulator")
    simulator = ChargePointSimulator(default_simulator.as_config())
    started, status, log_file = simulator.start()
    if started:
        store.simulators[default_simulator.pk] = simulator
    return started, f"{status}. Log: {log_file}"


def _process_action_requests(
    runtime_dir: Path, stop_event: threading.Event, stdout, instance_state: dict[str, object]
) -> None:
    request_path = runtime_dir / "data" / "pyxel_action_request.json"
    response_path = runtime_dir / "data" / "pyxel_action_response.json"
    last_token = ""

    while not stop_event.wait(0.5):
        try:
            payload = json.loads(request_path.read_text())
        except FileNotFoundError:
            continue
        except json.JSONDecodeError:
            continue

        token = payload.get("token")
        if not token or token == last_token:
            continue

        action = payload.get("action")
        success = False
        message = "Unknown action"
        if action == "start_default_simulator":
            close_old_connections()
            success, message = _start_default_simulator()
            instance_state["running"] = instance_state.get("running") or success
            close_old_connections()

        response = {
            "token": token,
            "success": success,
            "message": message,
            "completed_at": timezone.now().isoformat(),
        }
        try:
            _atomic_write_json(response_path, response)
        except Exception as exc:  # pragma: no cover - defensive logging
            stdout.write(f"Failed to write action response: {exc}")
        last_token = token


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
        parser.add_argument(
            "--ensure-instance",
            action="store_true",
            help=(
                "Start a local instance for the viewport when none is reachable. "
                "The process stops automatically when the Pyxel window closes."
            ),
        )

    def handle(self, *args, **options):
        output_dir_option = options.get("output_dir")
        pyxel_runner = options.get("pyxel_runner")
        skip_launch = options.get("skip_launch")
        ensure_instance = options.get("ensure_instance")

        runtime_dir, is_temp_dir = _normalize_output_dir(output_dir_option)

        base_dir = Path(settings.BASE_DIR)
        instance_running, instance_port = _detect_instance(base_dir)
        instance_state: dict[str, object] = {"running": instance_running, "port": instance_port}
        instance_process: subprocess.Popen[bytes] | None = None
        instance_started = False
        instance_log_handle = None

        if ensure_instance and instance_running:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Detected an existing instance listening on port {instance_port}; reusing it."
                )
            )
        elif ensure_instance and not instance_running:
            instance_log_path = runtime_dir / "instance.log"
            instance_log_handle = instance_log_path.open("w")
            self.stdout.write(
                self.style.WARNING(
                    f"No running instance detected; starting a local server on port {instance_port}."
                )
            )
            try:
                instance_process = _start_instance(base_dir, instance_port, stdout=instance_log_handle)
            except Exception:
                instance_log_handle.close()
                raise
            instance_started = True
            instance_state["running"] = _probe_port(instance_port)

        connectors = (
            Charger.objects.exclude(connector_id__isnull=True)
            .order_by("charger_id", "connector_id")
            .select_related("location")
        )
        snapshot = _connector_snapshot(connectors, instance_running=instance_state["running"])
        if not snapshot["connectors"]:
            self.stdout.write(
                self.style.WARNING(
                    "No connectors with a connector id were found. The viewport will start with an empty list."
                )
            )

        _copy_project_assets(runtime_dir)
        snapshot_path = _write_snapshot(runtime_dir, snapshot)
        self.stdout.write(self.style.SUCCESS(f"Connector snapshot written to {snapshot_path}"))

        if skip_launch:
            self.stdout.write(
                "Skipping Pyxel launch; run `pyxel run main.py` from %s to open the viewport." % runtime_dir
            )
            if instance_started and instance_process:
                instance_process.terminate()
                try:
                    instance_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    instance_process.kill()
            if instance_log_handle:
                instance_log_handle.close()
            return

        cleanup_required = is_temp_dir
        stop_event = threading.Event()
        threads = [
            threading.Thread(
                target=_refresh_snapshot_periodically,
                args=(runtime_dir, stop_event, instance_state, self.stdout),
                daemon=True,
            ),
            threading.Thread(
                target=_process_action_requests,
                args=(runtime_dir, stop_event, self.stdout, instance_state),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        try:
            runner_parts = _resolve_pyxel_runner(pyxel_runner)
            _launch_pyxel(runner_parts, runtime_dir)
        finally:
            stop_event.set()
            for thread in threads:
                thread.join(timeout=2)
            if instance_started and instance_process:
                instance_process.terminate()
                try:
                    instance_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    instance_process.kill()
            if instance_log_handle:
                instance_log_handle.close()
            if cleanup_required:
                shutil.rmtree(runtime_dir, ignore_errors=True)
