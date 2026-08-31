from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
import json
import logging
import os

from django.conf import settings
from django.utils import timezone

from apps.core.uptime_constants import SUITE_UPTIME_LOCK_MAX_AGE, SUITE_UPTIME_LOCK_NAME


AUTO_UPGRADE_LOCK_NAME = "auto_upgrade.lck"
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
STARTUP_REPORT_LOG_NAME = "startup-report.log"


logger = logging.getLogger(__name__)


def _auto_upgrade_mode_file(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_LOCK_NAME


def _auto_upgrade_skip_file(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


def _clear_auto_upgrade_skip_revisions(base_dir: Path) -> None:
    """Remove recorded skip revisions so future upgrade attempts proceed."""

    skip_file = _auto_upgrade_skip_file(base_dir)
    try:
        skip_file.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to remove auto-upgrade skip lockfile: %s", exc)


def _suite_uptime_lock_path(base_dir: Path | str | None = None) -> Path:
    """Return the lockfile path used to store suite uptime metadata."""

    root = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
    return root / ".locks" / SUITE_UPTIME_LOCK_NAME


def _suite_uptime_lock_info(*, now: datetime | None = None) -> dict[str, object]:
    """Return parsed metadata for the suite uptime lock file."""

    current_time = now or timezone.now()
    lock_path = _suite_uptime_lock_path()
    info: dict[str, object] = {
        "path": lock_path,
        "exists": False,
        "started_at": None,
        "fresh": False,
    }

    try:
        lock_path.stat()
    except OSError:
        return info

    info["exists"] = True
    try:
        raw_payload = lock_path.read_text(encoding="utf-8")
    except OSError:
        raw_payload = ""

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}

    started_at = _parse_suite_uptime_timestamp(
        payload.get("started_at") or payload.get("boot_time")
    )
    info["started_at"] = started_at
    info["fresh"] = bool(
        started_at
        and started_at <= current_time
        and _suite_uptime_lock_is_fresh(lock_path, current_time)
    )

    return info


def _parse_suite_uptime_timestamp(value: object) -> datetime | None:
    """Parse an ISO timestamp from the suite uptime lock file."""

    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text[-1] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        try:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        except Exception:
            return None

    return parsed


def _suite_uptime_lock_is_fresh(lock_path: Path, now: datetime) -> bool:
    """Return ``True`` when the lockfile heartbeat is within the freshness window."""

    try:
        stats = lock_path.stat()
    except OSError:
        return False

    heartbeat = datetime.fromtimestamp(stats.st_mtime, tz=datetime_timezone.utc)
    now_utc = now.astimezone(datetime_timezone.utc)
    if heartbeat > now_utc:
        return False
    return (now_utc - heartbeat) <= SUITE_UPTIME_LOCK_MAX_AGE


def _startup_report_log_path(base_dir: Path | None = None) -> Path:
    root = Path(settings.BASE_DIR) if base_dir is None else Path(base_dir)
    return root / "logs" / STARTUP_REPORT_LOG_NAME


def _startup_report_reference_time(log_path: Path) -> datetime | None:
    """Return the log's modification time in the current timezone."""

    try:
        mtime = log_path.stat().st_mtime
    except OSError:
        return None

    try:
        return timezone.make_aware(datetime.fromtimestamp(mtime))
    except (OverflowError, ValueError, OSError):
        return None


def _read_service_mode(lock_dir: Path) -> str:
    lock_path = lock_dir / "service_mode.lck"
    try:
        return lock_path.read_text(encoding="utf-8").strip().lower() or "embedded"
    except FileNotFoundError:
        return "embedded"
    except OSError:
        logger.warning("Failed to read service mode from %s", lock_path)
        return "embedded"


def _pid_file_running(pid_path: Path) -> bool:
    try:
        raw_pid = pid_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Failed to read PID file %s: %s", pid_path, exc)
        return False

    if not raw_pid.isdigit():
        return False

    pid = int(raw_pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _configured_backend_port(base_dir: Path) -> int:
    lock_file = base_dir / ".locks" / "backend_port.lck"
    try:
        raw = lock_file.read_text().strip()
    except OSError:
        return 8888
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 8888
    if 1 <= value <= 65535:
        return value
    return 8888


def _resolve_nginx_mode(base_dir: Path) -> str:
    """Return the configured nginx mode with a safe fallback."""

    mode_file = base_dir / ".locks" / "nginx_mode.lck"
    try:
        raw_mode = mode_file.read_text().strip()
    except OSError:
        return "internal"

    normalized = raw_mode.lower() or "internal"
    if normalized not in {"internal", "public"}:
        return "internal"
    return normalized


def _nginx_site_path() -> Path:
    configured_path = getattr(settings, "NGINX_SITE_PATH", None) or ""
    if configured_path:
        return Path(configured_path)
    return Path("/etc/nginx/sites-enabled/arthexis.conf")
