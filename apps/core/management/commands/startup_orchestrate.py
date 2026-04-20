"""Coordinate startup preflight and runtime launch decisions for service-start."""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.core.system.filesystem import _read_service_mode
from apps.nodes.tasks import send_startup_net_message
from apps.screens.startup_notifications import lcd_feature_enabled


ARTHEXIS_SERVICE_MODE_EMBEDDED = "embedded"
ARTHEXIS_SERVICE_MODE_SYSTEMD = "systemd"
SYSTEMD_UNITS_LOCK = "systemd_services.lck"


class Command(BaseCommand):
    help = "Run startup orchestration and emit process-launch decisions as JSON."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--port", required=True, help="Backend port for startup metadata.")
        parser.add_argument(
            "--lock-dir",
            default=None,
            help="Lock directory path (defaults to BASE_DIR/.locks).",
        )
        parser.add_argument(
            "--service-name",
            default=None,
            help="Service name override (defaults to value in service.lck).",
        )
        parser.add_argument(
            "--service-mode",
            default=None,
            choices=[ARTHEXIS_SERVICE_MODE_EMBEDDED, ARTHEXIS_SERVICE_MODE_SYSTEMD],
            help="Service mode override (defaults to lock-driven mode).",
        )
        parser.add_argument(
            "--celery-mode",
            default="auto",
            choices=["auto", "embedded", "systemd", "disabled"],
            help="Celery launch mode policy.",
        )

    def handle(self, *args, **options):
        started_monotonic = time.monotonic()
        started_at_epoch = int(time.time())
        started_at_iso = datetime.now(timezone.utc).isoformat()
        phase_timings: list[dict[str, object]] = []

        base_dir = Path(settings.BASE_DIR)
        lock_dir = Path(options.get("lock_dir") or (base_dir / ".locks"))
        lock_dir.mkdir(parents=True, exist_ok=True)

        startup_started_lock = lock_dir / "startup_started_at.lck"
        startup_orchestrate_lock = lock_dir / "startup_orchestrate_status.lck"

        service_name = (options.get("service_name") or "").strip()
        if not service_name:
            service_name = self._read_lock_line(lock_dir / "service.lck")

        service_mode = options.get("service_mode") or _read_service_mode(lock_dir)
        celery_mode = options["celery_mode"]

        systemd_units = self._read_systemd_units(lock_dir)
        systemd_celery_units = self._has_systemd_celery_units(service_name, systemd_units)
        lcd_systemd_unit = self._has_unit(
            service_name=service_name,
            units=systemd_units,
            template="lcd-{service_name}.service",
        )
        lcd_enabled = lcd_feature_enabled(lock_dir)

        payload: dict[str, object] = {
            "status": "ok",
            "port": str(options["port"]),
            "started_at_epoch": started_at_epoch,
            "started_at": started_at_iso,
            "paths": {
                "startup_started": str(startup_started_lock),
                "startup_orchestrate": str(startup_orchestrate_lock),
            },
            "service": {
                "name": service_name,
                "mode": service_mode,
                "systemd_celery_units": systemd_celery_units,
                "lcd_systemd_unit": lcd_systemd_unit,
            },
            "features": {
                "lcd_enabled": lcd_enabled,
            },
            "checks": [],
        }

        self._write_startup_started_lock(startup_started_lock, started_at_epoch)

        preflight_ok, preflight_status = self._timed_step(
            "runserver_preflight",
            lambda: self._run_preflight(lock_dir=lock_dir, base_dir=base_dir),
            orchestrate_started_monotonic=started_monotonic,
        )
        payload["checks"].append(preflight_status)
        phase_timings.append(preflight_status)

        maintenance_ok, maintenance_status = self._timed_step(
            "startup_maintenance",
            self._run_startup_maintenance,
            orchestrate_started_monotonic=started_monotonic,
        )
        payload["checks"].append(maintenance_status)
        phase_timings.append(maintenance_status)

        startup_message_status = "skipped:lcd-disabled"
        if lcd_enabled:
            startup_message_status, startup_message_timing = self._timed_startup_message(
                port=str(options["port"]),
                orchestrate_started_monotonic=started_monotonic,
            )
        else:
            phase_started_monotonic = time.monotonic()
            phase_finished_monotonic = time.monotonic()
            startup_message_timing = self._build_phase_timing(
                name="startup_message",
                detail=startup_message_status,
                phase_started_epoch=time.time(),
                phase_finished_epoch=time.time(),
                phase_started_monotonic=phase_started_monotonic,
                phase_finished_monotonic=phase_finished_monotonic,
                orchestrate_started_monotonic=started_monotonic,
                status="skipped",
            )
        payload["startup_message_status"] = startup_message_status
        phase_timings.append(startup_message_timing)

        celery_embedded = self._resolve_celery_embedded(
            celery_mode=celery_mode,
            service_mode=service_mode,
        )
        lcd_target_mode = self._resolve_lcd_target_mode(
            lcd_enabled=lcd_enabled,
            service_mode=service_mode,
            lcd_systemd_unit=lcd_systemd_unit,
        )

        payload["launch"] = {
            "celery_embedded": celery_embedded,
            "lcd_embedded": lcd_enabled and lcd_target_mode == ARTHEXIS_SERVICE_MODE_EMBEDDED,
            "lcd_target_mode": lcd_target_mode,
        }
        payload["phase_timings"] = phase_timings

        if not preflight_ok or not maintenance_ok:
            payload["status"] = "error"

        orchestration_duration = max(int(time.monotonic() - started_monotonic), 0)
        self._write_duration_lock(
            lock_path=startup_orchestrate_lock,
            started_at_epoch=started_at_epoch,
            duration_seconds=orchestration_duration,
            status=0 if payload["status"] == "ok" else 1,
            phase="orchestration",
            port=str(options["port"]),
            phase_timings=phase_timings,
        )
        self.stdout.write(json.dumps(payload, sort_keys=True))

        if payload["status"] != "ok":
            raise CommandError("startup orchestration failed")

    def _run_preflight(self, *, lock_dir: Path, base_dir: Path) -> tuple[bool, dict[str, str]]:
        helper_script = base_dir / "scripts" / "helpers" / "runserver_preflight.sh"
        if not helper_script.is_file():
            return False, {"name": "runserver_preflight", "status": "error", "detail": "helper script missing"}

        quoted_helper = shlex.quote(str(helper_script))
        env = {
            **os.environ,
            "ARTHEXIS_PYTHON_BIN": sys.executable,
            "BASE_DIR": str(base_dir),
            "LOCK_DIR": str(lock_dir),
        }
        command = f"source {quoted_helper} && run_runserver_preflight"
        result = subprocess.run(
            ["bash", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode == 0:
            detail = "ok"
            if result.stdout.strip():
                detail = result.stdout.strip().splitlines()[-1]
            return True, {"name": "runserver_preflight", "status": "ok", "detail": detail}

        stderr = (result.stderr or "").strip().splitlines()
        detail = stderr[-1] if stderr else "runserver preflight failed"
        return False, {"name": "runserver_preflight", "status": "error", "detail": detail}

    def _run_startup_maintenance(self) -> tuple[bool, dict[str, str]]:
        stdout = StringIO()
        stderr = StringIO()
        try:
            call_command("startup_maintenance", stdout=stdout, stderr=stderr)
        except CommandError as exc:
            detail = stderr.getvalue().strip() or stdout.getvalue().strip() or str(exc)
            return False, {
                "name": "startup_maintenance",
                "status": "error",
                "detail": detail,
            }
        except Exception as exc:
            detail = stderr.getvalue().strip() or stdout.getvalue().strip() or str(exc)
            return False, {
                "name": "startup_maintenance",
                "status": "error",
                "detail": detail,
            }
        return True, {"name": "startup_maintenance", "status": "ok", "detail": "ok"}

    @staticmethod
    def _queue_startup_message(port: str) -> str:
        try:
            return send_startup_net_message(port=port)
        except Exception as exc:
            return f"error:{exc}"

    def _timed_startup_message(
        self,
        *,
        port: str,
        orchestrate_started_monotonic: float,
    ) -> tuple[str, dict[str, object]]:
        phase_started_monotonic = time.monotonic()
        phase_started_epoch = time.time()
        detail = self._queue_startup_message(port)
        phase_finished_epoch = time.time()
        phase_finished_monotonic = time.monotonic()
        status = "error" if str(detail).startswith("error:") else "ok"
        return detail, self._build_phase_timing(
            name="startup_message",
            detail=detail,
            phase_started_epoch=phase_started_epoch,
            phase_finished_epoch=phase_finished_epoch,
            phase_started_monotonic=phase_started_monotonic,
            phase_finished_monotonic=phase_finished_monotonic,
            orchestrate_started_monotonic=orchestrate_started_monotonic,
            status=status,
        )

    def _timed_step(
        self,
        name: str,
        callback,
        *,
        orchestrate_started_monotonic: float,
    ) -> tuple[bool, dict[str, object]]:
        phase_started_monotonic = time.monotonic()
        phase_started_epoch = time.time()
        ok, status = callback()
        phase_finished_epoch = time.time()
        phase_finished_monotonic = time.monotonic()
        status_payload = dict(status)
        status_payload.update(
            self._build_phase_timing(
                name=name,
                detail=str(status.get("detail") or ""),
                phase_started_epoch=phase_started_epoch,
                phase_finished_epoch=phase_finished_epoch,
                phase_started_monotonic=phase_started_monotonic,
                phase_finished_monotonic=phase_finished_monotonic,
                orchestrate_started_monotonic=orchestrate_started_monotonic,
                status=str(status.get("status") or ("ok" if ok else "error")),
            )
        )
        return ok, status_payload

    @staticmethod
    def _build_phase_timing(
        *,
        name: str,
        detail: str,
        phase_started_epoch: float,
        phase_finished_epoch: float,
        phase_started_monotonic: float,
        phase_finished_monotonic: float,
        orchestrate_started_monotonic: float,
        status: str,
    ) -> dict[str, object]:
        duration_ms = max(int(round((phase_finished_monotonic - phase_started_monotonic) * 1000)), 0)
        started_offset_ms = max(
            int(round((phase_started_monotonic - orchestrate_started_monotonic) * 1000)),
            0,
        )
        return {
            "name": name,
            "detail": detail,
            "status": status,
            "duration_ms": duration_ms,
            "started_offset_ms": started_offset_ms,
            "started_at": datetime.fromtimestamp(phase_started_epoch, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(phase_finished_epoch, tz=timezone.utc).isoformat(),
        }

    @staticmethod
    def _read_lock_line(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _read_systemd_units(lock_dir: Path) -> set[str]:
        path = lock_dir / SYSTEMD_UNITS_LOCK
        try:
            return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
        except OSError:
            return set()

    def _has_systemd_celery_units(self, service_name: str, units: set[str]) -> bool:
        if not service_name:
            return False
        return any(
            self._has_unit(service_name=service_name, units=units, template=template)
            for template in ("celery-{service_name}.service", "celery-beat-{service_name}.service")
        )

    @staticmethod
    def _has_unit(*, service_name: str, units: set[str], template: str) -> bool:
        if not service_name:
            return False
        return template.format(service_name=service_name) in units

    @staticmethod
    def _resolve_celery_embedded(*, celery_mode: str, service_mode: str) -> bool:
        if celery_mode == "disabled":
            return False
        if celery_mode == "embedded":
            return True
        if celery_mode == "systemd":
            return False
        return service_mode != ARTHEXIS_SERVICE_MODE_SYSTEMD

    @staticmethod
    def _resolve_lcd_target_mode(*, lcd_enabled: bool, service_mode: str, lcd_systemd_unit: bool) -> str:
        if not lcd_enabled:
            return ARTHEXIS_SERVICE_MODE_EMBEDDED
        if service_mode == ARTHEXIS_SERVICE_MODE_SYSTEMD and lcd_systemd_unit:
            return ARTHEXIS_SERVICE_MODE_SYSTEMD
        return ARTHEXIS_SERVICE_MODE_EMBEDDED

    @staticmethod
    def _write_startup_started_lock(lock_path: Path, started_at_epoch: int) -> None:
        lock_path.write_text(f"{started_at_epoch}\n", encoding="utf-8")

    @staticmethod
    def _write_duration_lock(
        *,
        lock_path: Path,
        started_at_epoch: int,
        duration_seconds: int,
        status: int,
        phase: str,
        port: str,
        phase_timings: list[dict[str, object]] | None = None,
    ) -> None:
        finished_at_epoch = int(time.time())
        payload = {
            "started_at": datetime.fromtimestamp(started_at_epoch, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(finished_at_epoch, tz=timezone.utc).isoformat(),
            "duration_seconds": duration_seconds,
            "status": status,
            "phase": phase,
            "port": port,
            "phase_timings": phase_timings or [],
        }
        lock_path.write_text(json.dumps(payload), encoding="utf-8")
