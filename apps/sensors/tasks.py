from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
import re

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Thermometer, UsbTracker
from .thermometers import read_w1_temperature

logger = logging.getLogger(__name__)

USB_TRACKER_DEFAULT_ROOTS = ("/media", "/run/media", "/mnt")
USB_TRACKER_MAX_BYTES = 128 * 1024


def _thermometer_is_due(thermometer: Thermometer, now: datetime) -> bool:
    interval_seconds = thermometer.sampling_interval_seconds
    if interval_seconds <= 0:
        return False
    last_read_at = thermometer.last_read_at
    if last_read_at is None:
        return True
    return (now - last_read_at).total_seconds() >= interval_seconds


@shared_task(name="apps.sensors.tasks.sample_thermometers")
def sample_thermometers() -> dict[str, int]:
    now = timezone.localtime()
    sampled = 0
    skipped = 0
    failed = 0

    for thermometer in Thermometer.objects.filter(is_active=True).iterator():
        if not _thermometer_is_due(thermometer, now):
            skipped += 1
            continue

        path_template = getattr(
            settings,
            "THERMOMETER_PATH_TEMPLATE",
            "/sys/bus/w1/devices/{slug}/temperature",
        )
        device_path = path_template.format(slug=thermometer.slug)
        reading = read_w1_temperature(paths=[device_path])
        if reading is None:
            failed += 1
            logger.info(
                "Thermometer sample skipped; no reading returned for %s",
                thermometer.slug,
            )
            continue

        thermometer.record_reading(reading, read_at=timezone.localtime())
        sampled += 1

    return {"sampled": sampled, "skipped": skipped, "failed": failed}


def _usb_mount_roots() -> tuple[Path, ...]:
    roots = getattr(settings, "USB_TRACKER_MOUNT_ROOTS", USB_TRACKER_DEFAULT_ROOTS)
    return tuple(Path(root) for root in roots)


def _iter_usb_mounts(roots: tuple[Path, ...]) -> list[Path]:
    mounts: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("*", "*/*"):
            try:
                for path in root.glob(pattern):
                    if path.is_dir():
                        mounts.add(path)
            except OSError:
                continue
    return list(mounts)


def _read_match_file(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(USB_TRACKER_MAX_BYTES)
    except OSError:
        return None


def _format_recipe_result(result: object) -> str:
    if result is None:
        return ""
    try:
        return str(result)
    except Exception:
        return repr(result)


def _escape_recipe_arg(value: str) -> str:
    escaped = value.encode("unicode_escape").decode("ascii")
    escaped = escaped.replace('"', '\\"')
    return escaped.replace("'", "\\'")


def _match_usb_tracker(
    tracker: UsbTracker,
    mount: Path,
) -> tuple[str, Path, Path] | None:
    relative_path = tracker.required_file_path.lstrip("/")
    if not relative_path:
        return None
    candidate = (mount / relative_path).resolve()
    if not candidate.is_relative_to(mount):
        return None
    if not candidate.exists():
        return None

    if tracker.required_file_regex:
        content = _read_match_file(candidate)
        if content is None:
            return None
        try:
            if not re.search(tracker.required_file_regex, content, flags=re.MULTILINE):
                return None
        except re.error as exc:
            raise ValueError(f"Invalid regex for tracker {tracker.slug}: {exc}") from exc

    try:
        stats = candidate.stat()
    except OSError:
        return None

    signature = f"{candidate}:{stats.st_size}:{stats.st_mtime_ns}"
    return signature, candidate, mount


@shared_task(name="apps.sensors.tasks.scan_usb_trackers")
def scan_usb_trackers() -> dict[str, int]:
    now = timezone.localtime()
    mounts = _iter_usb_mounts(_usb_mount_roots())
    scanned = 0
    matched = 0
    triggered = 0
    failed = 0

    for tracker in UsbTracker.objects.filter(is_active=True).iterator():
        scanned += 1
        update_fields = ["last_checked_at"]
        tracker.last_checked_at = now

        match_info = None
        for mount in mounts:
            try:
                match_info = _match_usb_tracker(tracker, mount)
            except ValueError as exc:
                tracker.last_error = str(exc)
                update_fields.append("last_error")
                match_info = None
                break
            if match_info:
                break

        if not match_info:
            tracker.save(update_fields=update_fields)
            continue

        matched += 1
        signature, match_path, mount = match_info
        tracker.last_matched_at = now
        tracker.last_match_path = str(match_path)
        update_fields.extend(["last_matched_at", "last_match_path"])

        if signature == tracker.last_match_signature:
            tracker.save(update_fields=update_fields)
            continue

        if tracker.last_triggered_at and tracker.cooldown_seconds:
            elapsed = (now - tracker.last_triggered_at).total_seconds()
            if elapsed < tracker.cooldown_seconds:
                tracker.save(update_fields=update_fields)
                continue

        tracker.last_match_signature = signature
        tracker.last_triggered_at = now
        update_fields.extend(["last_match_signature", "last_triggered_at"])

        if tracker.recipe:
            try:
                result = tracker.recipe.execute(
                    tracker=tracker,
                    mount_path=_escape_recipe_arg(str(mount)),
                    file_path=_escape_recipe_arg(str(match_path)),
                )
                tracker.last_recipe_result = _format_recipe_result(result.result)
                tracker.last_error = ""
                triggered += 1
                update_fields.extend(["last_recipe_result", "last_error"])
            except Exception as exc:  # pragma: no cover - defensive logging
                tracker.last_error = str(exc)
                tracker.last_recipe_result = ""
                failed += 1
                update_fields.extend(["last_error", "last_recipe_result"])
        else:
            tracker.last_recipe_result = ""
            tracker.last_error = ""
            update_fields.extend(["last_recipe_result", "last_error"])

        tracker.save(update_fields=update_fields)

    return {
        "scanned": scanned,
        "matched": matched,
        "triggered": triggered,
        "failed": failed,
    }


__all__ = ["sample_thermometers", "scan_usb_trackers"]
