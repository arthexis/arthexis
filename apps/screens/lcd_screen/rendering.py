"""Payload formatting, scrolling, and display state logic for the LCD screen."""

from __future__ import annotations

import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from glob import glob
from itertools import cycle, islice
from pathlib import Path
from typing import Callable, NamedTuple

import psutil

from apps.screens.animations import AnimationLoadError, default_tree_frames
from apps.core import uptime_utils

from . import locks
from .hardware import LCDFrameWriter, LCD_COLUMNS, LCD_ROWS
from .logging import BASE_DIR

logger = logging.getLogger(__name__)

MIN_SCROLL_MS = 50
SCROLL_PADDING = 3
DEFAULT_FALLBACK_SCROLL_SEC = 0.5
CLOCK_TIME_FORMAT = "%p %I:%M"
CLOCK_DATE_FORMAT = "%Y-%m-%d %a"
GAP_ANIMATION_FRAMES_PER_PAYLOAD = 4
GAP_ANIMATION_SCROLL_MS = 600

try:
    GAP_ANIMATION_FRAMES = default_tree_frames()
except AnimationLoadError:
    logger.debug("Falling back to blank animation frames", exc_info=True)
    GAP_ANIMATION_FRAMES = [" " * (LCD_COLUMNS * LCD_ROWS)]

GAP_ANIMATION_CYCLE = cycle(GAP_ANIMATION_FRAMES)


def _package_override(name: str, default):
    package = sys.modules.get("apps.screens.lcd_screen")
    if package is None:
        return default
    return getattr(package, name, default)


class FateDeck:
    """Shuffle and draw from a 55-card fate deck."""

    suits = ("D", "H", "C", "V")
    values = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "X", "J", "Q", "K")
    jokers = ("XX", "XY", "YY")

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._cards: list[str] = []
        self._reshuffle()

    def _reshuffle(self) -> None:
        deck = [f"{suit}{value}" for suit in self.suits for value in self.values]
        deck.extend(self.jokers)
        self.rng.shuffle(deck)
        self._cards = deck

    def draw(self) -> str:
        if not self._cards:
            self._reshuffle()
        return self._cards.pop()

    @property
    def remaining(self) -> int:
        return len(self._cards)


FATE_VECTOR = ""
_fate_deck = FateDeck()


class DisplayState(NamedTuple):
    pad1: str
    pad2: str
    steps1: int
    steps2: int
    index1: int
    index2: int
    scroll_sec: float
    cycle: int
    last_segment1: str | None
    last_segment2: str | None


_NON_ASCII_CACHE: set[str] = set()


@dataclass
class ChannelCycle:
    payloads: list[locks.LockPayload]
    signature: tuple[tuple[int, float], ...]
    index: int = 0

    def next_payload(self) -> locks.LockPayload | None:
        if not self.payloads:
            return None
        payload = self.payloads[self.index % len(self.payloads)]
        self.index = (self.index + 1) % len(self.payloads)
        return payload


def _non_ascii_positions(text: str) -> list[str]:
    printable = {9, 10, 13} | set(range(32, 127))
    return [f"0x{ord(ch):02x}@{idx}" for idx, ch in enumerate(text) if ord(ch) not in printable]


def _warn_on_non_ascii_payload(payload: locks.LockPayload, label: str) -> None:
    cache_key = (label, payload.line1, payload.line2)
    if cache_key in _NON_ASCII_CACHE:
        return

    issues = _non_ascii_positions(payload.line1) + _non_ascii_positions(payload.line2)
    if issues:
        logger.warning("Non-ASCII characters detected in %s payload: %s", label, ", ".join(issues))
        _NON_ASCII_CACHE.add(cache_key)


def _has_visible_text(text: str) -> bool:
    return any(ch.isprintable() and not ch.isspace() for ch in text)


def _payload_has_text(payload: locks.LockPayload) -> bool:
    return _has_visible_text(payload.line1) or _has_visible_text(payload.line2)


def _animation_payload(
    frame_cycle,
    *,
    frames_per_payload: int = GAP_ANIMATION_FRAMES_PER_PAYLOAD,
    scroll_ms: int = GAP_ANIMATION_SCROLL_MS,
) -> locks.LockPayload:
    frames = list(islice(frame_cycle, frames_per_payload))
    if not frames:
        return locks.LockPayload("", "", scroll_ms)

    line1 = " ".join(frame[:LCD_COLUMNS] for frame in frames).rstrip()
    line2 = " ".join(frame[LCD_COLUMNS:] for frame in frames).rstrip()
    return locks.LockPayload(line1, line2, scroll_ms)


def _select_low_payload(
    payload: locks.LockPayload,
    frame_cycle=GAP_ANIMATION_CYCLE,
    *,
    base_dir: Path = BASE_DIR,
    now: datetime | None = None,
    scroll_ms: int | None = None,
    frames_per_payload: int | None = None,
) -> locks.LockPayload:
    if _payload_has_text(payload):
        return payload

    now_value = now or datetime.now(datetime_timezone.utc)
    locks._install_date(base_dir, now=now_value)
    uptime_seconds = _package_override("_uptime_seconds", locks._uptime_seconds)
    uptime_secs = uptime_seconds(base_dir, now=now_value)
    uptime_label = _format_uptime_label(uptime_secs) or "?d?h?m"
    on_seconds = _package_override("_on_seconds", locks._on_seconds)
    on_label = _format_on_label(on_seconds(base_dir, now=now_value)) or "?m?s"
    subject_parts = [f"UP {uptime_label}"]
    ap_mode_enabled = _package_override("_ap_mode_enabled", _ap_mode_enabled)
    ap_client_count_func = _package_override("_ap_client_count", _ap_client_count)
    if ap_mode_enabled():
        ap_client_count = ap_client_count_func()
        if ap_client_count is None:
            subject_parts.append("AP")
        else:
            subject_parts.append(f"AP{ap_client_count}")
    subject = " ".join(subject_parts).strip()
    interface_label_func = _package_override(
        "_internet_interface_label", _internet_interface_label
    )
    interface_label = interface_label_func()
    body_parts = [f"ON {on_label}"]
    if interface_label:
        body_parts.append(interface_label)
    body = " ".join(body_parts).strip()
    return locks.LockPayload(
        subject,
        body,
        locks.DEFAULT_SCROLL_MS,
        is_base=True,
    )


def _apply_low_payload_fallback(payload: locks.LockPayload) -> locks.LockPayload:
    return _select_low_payload(
        payload,
        frame_cycle=GAP_ANIMATION_CYCLE,
        base_dir=BASE_DIR,
        scroll_ms=GAP_ANIMATION_SCROLL_MS,
    )


def _format_storage_value(value: int) -> str:
    if value <= 0:
        return "0B"

    for unit, factor in (
        ("T", 1024**4),
        ("G", 1024**3),
        ("M", 1024**2),
        ("K", 1024),
    ):
        if value >= factor:
            amount = value / factor
            if amount >= 10:
                formatted = f"{amount:.0f}"
            else:
                formatted = f"{amount:.1f}"
            if formatted.endswith(".0"):
                formatted = formatted[:-2]
            return f"{formatted}{unit}"
    return f"{value}B"


def _format_count(value: int) -> str:
    if value < 0:
        return "0"
    if value >= 1_000_000:
        formatted = f"{value / 1_000_000:.1f}M"
    elif value >= 10_000:
        formatted = f"{value / 1000:.0f}k"
    elif value >= 1000:
        formatted = f"{value / 1000:.1f}k"
    else:
        return str(value)
    if formatted.endswith(".0M") or formatted.endswith(".0k"):
        return formatted[:-2] + formatted[-1]
    return formatted


def _compact_stats_line(variants: list[str]) -> str:
    for variant in variants:
        if len(variant) <= LCD_COLUMNS:
            return variant
    return variants[-1][:LCD_COLUMNS]


def _stats_payload() -> locks.LockPayload:
    try:
        available_ram = psutil.virtual_memory().available
    except (psutil.Error, OSError):
        available_ram = None

    try:
        free_disk = psutil.disk_usage(str(BASE_DIR)).free
    except Exception:
        free_disk = None

    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
    except Exception:
        cpu_percent = None

    try:
        free_swap = psutil.swap_memory().free
    except Exception:
        free_swap = None

    ram_label = _format_storage_value(available_ram) if available_ram is not None else "?"
    disk_label = _format_storage_value(free_disk) if free_disk is not None else "?"
    if cpu_percent is not None:
        cpu_idle = max(0.0, min(100.0, 100.0 - cpu_percent))
        cpu_label = str(int(round(cpu_idle)))
    else:
        cpu_label = "?"
    swap_label = _format_storage_value(free_swap) if free_swap is not None else "?"

    line1 = _compact_stats_line(
        [
            f"RAM {ram_label} IDL{cpu_label}%",
            f"RAM{ram_label} IDL{cpu_label}%",
            f"RAM{ram_label} I{cpu_label}%",
            f"R{ram_label} I{cpu_label}%",
            f"R{ram_label}I{cpu_label}%",
        ]
    )
    line2 = _compact_stats_line(
        [
            f"DSK {disk_label} SWP{swap_label}",
            f"DSK{disk_label} SWP{swap_label}",
            f"DSK{disk_label}SWP{swap_label}",
            f"D{disk_label} SWP{swap_label}",
            f"D{disk_label}SWP{swap_label}",
        ]
    )
    return locks.LockPayload(
        line1,
        line2,
        locks.DEFAULT_SCROLL_MS,
        is_base=True,
    )


def _lcd_clock_enabled() -> bool:
    raw = (os.getenv("DISABLE_LCD_CLOCK") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def _lcd_temperature_label() -> str | None:
    try:
        label = _lcd_temperature_label_from_sensors()
    except Exception:
        logger.debug("Unable to load thermometer data", exc_info=True)
        label = None
    if label:
        return label
    try:
        return _lcd_temperature_label_from_sysfs()
    except Exception:
        logger.debug("Thermometer sysfs read failed", exc_info=True)
    return None


def _format_temperature_value(value: Decimal, unit: str) -> str:
    precision = ".0f" if value.copy_abs() >= Decimal("100") else ".1f"
    return f"{value:{precision}}{unit.upper()}"


def _parse_temperature_label(label: str) -> tuple[Decimal | None, str]:
    if not label:
        return None, ""

    unit = label[-1]
    try:
        value = Decimal(label[:-1])
    except (InvalidOperation, ValueError):
        return None, unit

    return value, unit


def _use_fate_vector() -> bool:
    return random.random() < 0.5


def _draw_fate_vector(deck: FateDeck | None = None) -> str:
    global FATE_VECTOR
    card = (deck or _fate_deck).draw()
    FATE_VECTOR = card
    package = sys.modules.get("apps.screens.lcd_screen")
    if package is not None:
        setattr(package, "FATE_VECTOR", card)
    return card


def _uptime_components(seconds: int | None) -> tuple[int, int, int] | None:
    if seconds is None or seconds < 0:
        return None

    minutes_total, _ = divmod(seconds, 60)
    days, remaining_minutes = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    return days, hours, minutes


def _ap_mode_enabled() -> bool:
    return uptime_utils.ap_mode_enabled()


def _ap_client_count() -> int | None:
    return uptime_utils.ap_client_count()


def _internet_interface_label() -> str:
    return uptime_utils.internet_interface_label()


def _format_uptime_label(seconds: int | None) -> str | None:
    components = _uptime_components(seconds)
    if components is None:
        return None

    days, hours, minutes = components
    return f"{days}d{hours}h{minutes}m"


def _format_on_label(seconds: int | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    minutes_total, secs = divmod(seconds, 60)
    return f"{minutes_total}m{secs}s"


def _refresh_uptime_payload(
    payload: locks.LockPayload, *, base_dir: Path = BASE_DIR, now: datetime | None = None
) -> locks.LockPayload:
    has_uptime = payload.line1.startswith("UP ")
    if not has_uptime:
        return payload

    now_value = now or datetime.now(datetime_timezone.utc)
    uptime_seconds = _package_override("_uptime_seconds", locks._uptime_seconds)
    uptime_secs = uptime_seconds(base_dir, now=now_value)
    uptime_label = _format_uptime_label(uptime_secs)
    if not uptime_label:
        return payload

    suffix = payload.line1[len("UP ") :].strip()
    extra_suffix = suffix.split(maxsplit=1)[1].strip() if " " in suffix else ""
    subject = f"UP {uptime_label}"
    if extra_suffix:
        subject = f"{subject} {extra_suffix}"

    line2 = payload.line2
    if line2.startswith("ON "):
        suffix = line2[len("ON ") :].strip()
        extra = suffix.split(maxsplit=1)[1].strip() if " " in suffix else ""
        on_seconds = _package_override("_on_seconds", locks._on_seconds)
        on_label = _format_on_label(on_seconds(base_dir, now=now_value)) or "?m?s"
        line2 = f"ON {on_label}"
        if extra:
            line2 = f"{line2} {extra}"

    return payload._replace(line1=subject, line2=line2)


def _lcd_temperature_label_from_sensors() -> str | None:
    return None


def _lcd_temperature_label_from_sysfs() -> str | None:
    try:
        from apps.sensors.thermometers import format_w1_temperature
    except Exception:
        format_w1_temperature = None

    if format_w1_temperature:
        try:
            label = format_w1_temperature()
        except Exception:
            logger.debug("Unable to load sysfs thermometer reading", exc_info=True)
        else:
            if label:
                value, unit = _parse_temperature_label(label)
                return _format_temperature_value(value, unit) if value else label

    for path in glob("/sys/bus/w1/devices/28-*/temperature"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if value.copy_abs() >= Decimal("1000"):
            value = value / Decimal("1000")
        return _format_temperature_value(value, "C")
    return None


def _clock_payload(
    now: datetime,
    *,
    use_fahrenheit: bool = False,
    fate_deck: FateDeck | None = None,
    choose_fate: Callable[[], bool] | None = None,
) -> tuple[str, str, int, str]:
    lcd_temperature_label = _package_override(
        "_lcd_temperature_label", _lcd_temperature_label
    )
    temperature = lcd_temperature_label()
    temp_value, unit = _parse_temperature_label(temperature or "")
    if temp_value is not None:
        if use_fahrenheit and unit.upper() == "C":
            temp_value = temp_value * Decimal("9") / Decimal("5") + Decimal("32")
            temperature = _format_temperature_value(temp_value, "F")
        else:
            temperature = _format_temperature_value(temp_value, unit)
    week_label = f"{now.isocalendar().week:02d}"
    date_label = f"{now.strftime(CLOCK_DATE_FORMAT)}{week_label}"
    fate = choose_fate or _use_fate_vector
    prefix = _draw_fate_vector(fate_deck) if fate() else now.strftime("%p")
    time_label = f"{prefix} {now.strftime('%I:%M')}"
    if temperature:
        time_label = f"{time_label} @ {temperature}"
    return (
        date_label,
        time_label,
        locks.DEFAULT_SCROLL_MS,
        "clock",
    )


def _display(
    lcd, line1: str, line2: str, scroll_ms: int
) -> None:
    state = _prepare_display_state(line1, line2, scroll_ms)
    _advance_display(state, LCDFrameWriter(lcd))


def _prepare_display_state(line1: str, line2: str, scroll_ms: int) -> DisplayState:
    if scroll_ms > 0:
        scroll_sec = max(scroll_ms, MIN_SCROLL_MS) / 1000.0
        text1 = line1[:64]
        text2 = line2[:64]
        pad1 = (
            text1 + " " * SCROLL_PADDING
            if len(text1) > LCD_COLUMNS
            else text1.ljust(LCD_COLUMNS)
        )
        pad2 = (
            text2 + " " * SCROLL_PADDING
            if len(text2) > LCD_COLUMNS
            else text2.ljust(LCD_COLUMNS)
        )
        steps1 = max(len(pad1) - (LCD_COLUMNS - 1), 1)
        steps2 = max(len(pad2) - (LCD_COLUMNS - 1), 1)
    else:
        scroll_sec = DEFAULT_FALLBACK_SCROLL_SEC
        pad1 = line1[:LCD_COLUMNS].ljust(LCD_COLUMNS)
        pad2 = line2[:LCD_COLUMNS].ljust(LCD_COLUMNS)
        steps1 = 1
        steps2 = 1
    cycle = math.lcm(steps1, steps2)
    return DisplayState(
        pad1,
        pad2,
        steps1,
        steps2,
        0,
        0,
        scroll_sec,
        cycle,
        None,
        None,
    )


def _advance_display(
    state: DisplayState,
    frame_writer: LCDFrameWriter,
    *,
    shutdown_requested: Callable[[], bool] | None = None,
    label: str | None = None,
    timestamp: datetime | None = None,
) -> tuple[DisplayState, bool, bool]:
    if shutdown_requested and shutdown_requested():
        return state, True, True

    segment1 = state.pad1[state.index1 : state.index1 + LCD_COLUMNS]
    segment2 = state.pad2[state.index2 : state.index2 + LCD_COLUMNS]

    write_required = segment1 != state.last_segment1 or segment2 != state.last_segment2
    write_success = True
    if write_required:
        write_success = frame_writer.write(
            segment1.ljust(LCD_COLUMNS),
            segment2.ljust(LCD_COLUMNS),
            label=label,
            timestamp=timestamp,
        )

    next_index1 = (state.index1 + 1) % state.steps1
    next_index2 = (state.index2 + 1) % state.steps2
    return (
        state._replace(
            index1=next_index1,
            index2=next_index2,
            last_segment1=segment1,
            last_segment2=segment2,
        ),
        write_success,
        False,
    )


class ScrollScheduler:
    """Ensure scroll cadence is driven by time rather than loop duration."""

    def __init__(self) -> None:
        self.next_deadline = time.monotonic()

    def sleep_until_ready(self) -> None:
        now = time.monotonic()
        if now < self.next_deadline:
            time.sleep(self.next_deadline - now)

    def advance(self, interval: float) -> None:
        now = time.monotonic()
        self.next_deadline = max(self.next_deadline + interval, now + interval)
