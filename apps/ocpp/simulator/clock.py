"""Clock abstraction for charger-originated simulator timestamps."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


UTC = timezone.utc


def parse_instant(value: str) -> datetime:
    """Parse an ISO-8601 instant and normalize it to UTC."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid charger clock start_time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class ChargerClock:
    """Produce charger time independently from simulator host execution time."""

    mode: str = "host"
    offset_seconds: float = 0.0
    start_time: str | None = None
    wall_time: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.mode = str(self.mode).lower()
        if self.mode not in {"host", "offset", "fixed", "frozen", "advancing"}:
            raise ValueError(
                "charger clock mode must be host, offset, fixed, frozen, or advancing"
            )
        self.offset_seconds = float(self.offset_seconds)
        self._anchor_monotonic = self.monotonic()
        self._anchor = parse_instant(self.start_time) if self.start_time else None
        if self.mode in {"fixed", "frozen", "advancing"} and self._anchor is None:
            raise ValueError(f"charger clock start_time is required for {self.mode}")

    @classmethod
    def from_profile(cls, config: dict[str, Any]) -> ChargerClock:
        return cls(
            mode=str(config.get("mode", "host")),
            offset_seconds=float(config.get("offset_seconds", 0.0)),
            start_time=(
                str(config["start_time"]) if config.get("start_time") is not None else None
            ),
        )

    def now(self) -> datetime:
        if self.mode == "host":
            return datetime.fromtimestamp(self.wall_time(), tz=UTC)
        if self.mode == "offset":
            return datetime.fromtimestamp(
                self.wall_time() + self.offset_seconds,
                tz=UTC,
            )
        if self.mode in {"fixed", "frozen"}:
            assert self._anchor is not None
            return self._anchor
        assert self._anchor is not None
        elapsed = self.monotonic() - self._anchor_monotonic
        return self._anchor + timedelta(seconds=elapsed)

    def isoformat(self) -> str:
        """Return charger time using OCPP-friendly UTC Z notation."""
        return self.now().isoformat().replace("+00:00", "Z")

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "offset_seconds": self.offset_seconds,
            "start_time": self.start_time,
            "charger_time": self.isoformat(),
        }
