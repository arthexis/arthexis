"""USB port status rendering for the LCD rotation."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from apps.screens.startup_notifications import LCD_USB_LOCK_FILE, write_lcd_message

from .constants import USB_LCD_EMPTY_LABEL, USB_LCD_LABEL_WIDTH, USB_LCD_PORT_COUNT
from .models import UsbPortMapping, UsbTracker

logger = logging.getLogger(__name__)

MICROPHONE_TERMS = ("microphone", "mic", "audio", "capture", "alsa")
CAMERA_TERMS = ("camera", "cam", "video", "v4l", "opencv", "rpicam")
BASTION_TERMS = ("bastion", "usb-key", "usb_key", "usb key", "security-key")


@dataclass(frozen=True)
class UsbPortStatus:
    """Rendered status for one physical USB hub port."""

    port_number: int
    label: str
    connected: bool
    source_type: str = ""
    source_identifier: str = ""


def normalize_usb_lcd_label(
    value: str | None, *, fallback: str = USB_LCD_EMPTY_LABEL
) -> str:
    """Return an ASCII LCD label constrained to the USB label width."""

    text = str(value or "").strip().upper()
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    text = re.sub(r"[^A-Z0-9 -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    return text[:USB_LCD_LABEL_WIDTH]


def default_usb_lcd_label(mapping: UsbPortMapping) -> str:
    """Resolve the LCD label for a connected mapping."""

    configured = normalize_usb_lcd_label(mapping.label, fallback="")
    if configured:
        return configured

    source_type = str(mapping.source_type)
    if source_type == UsbPortMapping.SourceType.RECORDING_DEVICE:
        return "LISTEN"
    if source_type == UsbPortMapping.SourceType.VIDEO_DEVICE:
        return "OBSERVE"

    descriptor = _mapping_descriptor(mapping)
    if _contains_any(descriptor, MICROPHONE_TERMS):
        return "LISTEN"
    if _contains_any(descriptor, CAMERA_TERMS):
        return "OBSERVE"
    if _contains_any(descriptor, BASTION_TERMS):
        return "BASTION"
    return normalize_usb_lcd_label(
        mapping.source_identifier or mapping.description or mapping.source_type
    )


def build_usb_lcd_statuses(
    mappings: Iterable[UsbPortMapping] | None = None,
    *,
    node=None,
) -> list[UsbPortStatus]:
    """Build four port statuses from local USB LCD mappings."""

    if mappings is None:
        mappings = UsbPortMapping.objects.filter(is_active=True).order_by("port_number")
    if node is None:
        node = _local_node()

    statuses = {
        port: UsbPortStatus(
            port_number=port, label=USB_LCD_EMPTY_LABEL, connected=False
        )
        for port in range(1, USB_LCD_PORT_COUNT + 1)
    }
    for mapping in mappings:
        port_number = int(mapping.port_number or 0)
        if not mapping.is_active or port_number not in statuses:
            continue
        connected = _mapping_connected(mapping, node=node)
        statuses[port_number] = UsbPortStatus(
            port_number=port_number,
            label=default_usb_lcd_label(mapping) if connected else USB_LCD_EMPTY_LABEL,
            connected=connected,
            source_type=str(mapping.source_type),
            source_identifier=mapping.source_identifier,
        )
    return [statuses[port] for port in range(1, USB_LCD_PORT_COUNT + 1)]


def render_usb_lcd_lines(statuses: Iterable[UsbPortStatus]) -> tuple[str, str]:
    """Render four USB port labels across two LCD rows."""

    labels = [
        normalize_usb_lcd_label(status.label)
        for status in list(statuses)[:USB_LCD_PORT_COUNT]
    ]
    while len(labels) < USB_LCD_PORT_COUNT:
        labels.append(USB_LCD_EMPTY_LABEL)
    return _render_label_pair(labels[0], labels[1]), _render_label_pair(
        labels[2], labels[3]
    )


def write_usb_lcd_status(*, lock_dir: Path | str | None = None) -> dict[str, object]:
    """Write the USB LCD lock file, or remove it when no mappings exist."""

    target_dir = (
        Path(lock_dir) if lock_dir is not None else Path(settings.BASE_DIR) / ".locks"
    )
    lock_file = target_dir / LCD_USB_LOCK_FILE
    try:
        mappings = list(
            UsbPortMapping.objects.filter(is_active=True).order_by("port_number")
        )
        configured = len(mappings)
        if configured <= 0:
            _remove_lock_file(lock_file)
            return {
                "configured": 0,
                "connected": 0,
                "written": False,
                "lock_file": str(lock_file),
            }

        statuses = build_usb_lcd_statuses(mappings=mappings)
    except (OperationalError, ProgrammingError) as exc:
        logger.debug(
            "Skipping USB LCD status refresh: database unavailable", exc_info=True
        )
        return {
            "configured": 0,
            "connected": 0,
            "written": False,
            "lock_file": str(lock_file),
            "error": str(exc),
        }

    line1, line2 = render_usb_lcd_lines(statuses)
    write_lcd_message(lock_file=lock_file, subject=line1, body=line2)
    return {
        "configured": configured,
        "connected": sum(1 for status in statuses if status.connected),
        "written": True,
        "lock_file": str(lock_file),
        "line1": line1,
        "line2": line2,
    }


def _render_label_pair(first: str, second: str) -> str:
    first_label = normalize_usb_lcd_label(first)
    second_label = normalize_usb_lcd_label(second)
    return f"{first_label:<{USB_LCD_LABEL_WIDTH}} {second_label:<{USB_LCD_LABEL_WIDTH}}".rstrip()


def _remove_lock_file(lock_file: Path) -> None:
    try:
        lock_file.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.debug("Failed to remove USB LCD lock file: %s", lock_file, exc_info=True)


def _local_node():
    try:
        from apps.nodes.models import Node

        return Node.get_local()
    except Exception:
        logger.debug("Unable to resolve local node for USB LCD status", exc_info=True)
        return None


def _mapping_connected(mapping: UsbPortMapping, *, node) -> bool:
    identifier = (mapping.source_identifier or "").strip()
    if not identifier:
        return False

    source_type = str(mapping.source_type)
    if source_type == UsbPortMapping.SourceType.USB_TRACKER:
        return (
            UsbTracker.objects.filter(slug=identifier, is_active=True)
            .exclude(last_match_path="")
            .exists()
        )
    if source_type == UsbPortMapping.SourceType.RECORDING_DEVICE:
        if node is None:
            return False
        from apps.audio.models import RecordingDevice

        queryset = RecordingDevice.objects.filter(
            identifier=identifier,
            capture_channels__gt=0,
        )
        if node is not None:
            queryset = queryset.filter(node=node)
        return queryset.exists()
    if source_type == UsbPortMapping.SourceType.VIDEO_DEVICE:
        if node is None:
            return False
        from apps.video.models import VideoDevice

        queryset = VideoDevice.objects.filter(identifier=identifier)
        if node is not None:
            queryset = queryset.filter(node=node)
        return queryset.exists()
    return False


def _mapping_descriptor(mapping: UsbPortMapping) -> str:
    parts = [mapping.label, mapping.source_identifier, mapping.description]
    if str(mapping.source_type) == UsbPortMapping.SourceType.USB_TRACKER:
        tracker = UsbTracker.objects.filter(slug=mapping.source_identifier).first()
        if tracker is not None:
            parts.extend([tracker.name, tracker.slug, tracker.description])
    return " ".join(str(part or "") for part in parts).lower()


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    normalized = value.lower()
    return any(term in normalized for term in terms)


__all__ = [
    "UsbPortStatus",
    "build_usb_lcd_statuses",
    "default_usb_lcd_label",
    "normalize_usb_lcd_label",
    "render_usb_lcd_lines",
    "write_usb_lcd_status",
]
