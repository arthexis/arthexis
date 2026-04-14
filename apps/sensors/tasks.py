from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Thermometer, UsbTracker
from .thermometers import read_temperature

logger = logging.getLogger(__name__)

USB_TRACKER_DEFAULT_ROOTS = ("/media", "/run/media", "/mnt")
USB_TRACKER_MAX_BYTES = 128 * 1024


def _thermometer_is_due(thermometer: Thermometer, now: datetime) -> bool:
    """Return whether the thermometer should be sampled now.

    Args:
        thermometer: Thermometer instance under consideration.
        now: Current timestamp used for interval comparison.

    Returns:
        ``True`` when the sampling interval has elapsed.
    """
    interval_seconds = thermometer.sampling_interval_seconds
    if interval_seconds <= 0:
        return False
    last_read_at = thermometer.last_read_at
    if last_read_at is None:
        return True
    return (now - last_read_at).total_seconds() >= interval_seconds


@shared_task(name="apps.sensors.tasks.sample_thermometers")
def sample_thermometers() -> dict[str, int]:
    """Sample all active thermometers that are due for a reading.

    Returns:
        Counters describing sampled, skipped, and failed thermometers.
    """
    now = timezone.localtime()
    sampled = 0
    skipped = 0
    failed = 0

    for thermometer in Thermometer.objects.filter(is_active=True).iterator():
        if not _thermometer_is_due(thermometer, now):
            skipped += 1
            continue

        source = str(getattr(settings, "THERMOMETER_SOURCE", "auto")).strip().lower()
        w1_path_template = getattr(
            settings,
            "THERMOMETER_PATH_TEMPLATE",
            "/sys/bus/w1/devices/{slug}/temperature",
        )
        w1_paths = [w1_path_template.format(slug=thermometer.slug)]
        i2c_path_template = str(
            getattr(settings, "THERMOMETER_I2C_PATH_TEMPLATE", "")
        ).strip()
        i2c_paths = (
            [i2c_path_template.format(slug=thermometer.slug)]
            if i2c_path_template
            else None
        )
        reading = read_temperature(source=source, w1_paths=w1_paths, i2c_paths=i2c_paths)
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
    """Return the configured directories that may contain USB mounts.

    Returns:
        Candidate mount root paths.
    """
    roots = getattr(settings, "USB_TRACKER_MOUNT_ROOTS", USB_TRACKER_DEFAULT_ROOTS)
    return tuple(Path(root) for root in roots)


def _iter_usb_mounts(roots: tuple[Path, ...]) -> list[Path]:
    """Enumerate mounted USB directories below the configured roots.

    Args:
        roots: Root directories to search.

    Returns:
        Unique mount directories found under the roots.
    """
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
    """Read a candidate match file up to the tracker byte limit.

    Args:
        path: File path to read.

    Returns:
        File contents, or ``None`` when the file cannot be read.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(USB_TRACKER_MAX_BYTES)
    except OSError:
        return None


def _match_usb_tracker(
    tracker: UsbTracker,
    mount: Path,
) -> Path | None:
    """Return the first passive USB tracker match for a mount.

    Args:
        tracker: USB tracker configuration.
        mount: Mount directory to inspect.

    Returns:
        The matching file path when the tracker matches, otherwise ``None``.

    Raises:
        ValueError: If the configured regex is invalid.
    """
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

    return candidate


@shared_task(name="apps.sensors.tasks.scan_usb_trackers")
def scan_usb_trackers() -> dict[str, int]:
    """Scan configured USB trackers and update their passive match state.

    Returns:
        Counters describing scanned, matched, and failed trackers.
    """
    now = timezone.localtime()
    mounts = _iter_usb_mounts(_usb_mount_roots())
    scanned = 0
    matched = 0
    failed = 0

    for tracker in UsbTracker.objects.filter(is_active=True).iterator():
        scanned += 1
        tracker.last_checked_at = now
        tracker.last_error = ""
        update_fields = ["last_checked_at", "last_error"]

        match_info = None
        for mount in mounts:
            try:
                match_info = _match_usb_tracker(tracker, mount)
            except ValueError as exc:
                tracker.last_error = str(exc)
                failed += 1
                match_info = None
                break
            if match_info:
                break

        if not match_info:
            tracker.last_match_path = ""
            update_fields.append("last_match_path")
            tracker.save(update_fields=update_fields)
            continue

        matched += 1
        match_path = match_info
        tracker.last_matched_at = now
        tracker.last_match_path = str(match_path)
        update_fields.extend(["last_matched_at", "last_match_path"])
        tracker.save(update_fields=update_fields)

    return {
        "scanned": scanned,
        "matched": matched,
        "failed": failed,
    }


__all__ = ["sample_thermometers", "scan_usb_trackers"]
