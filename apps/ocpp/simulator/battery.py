"""Deterministic battery-demand model for simulator charging sessions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DEFAULT_BATTERY_KWH = 60.0
DEFAULT_MAX_POWER_W = 7200.0
DEFAULT_LINE_VOLTAGE_V = 240.0
DEFAULT_TAPER_START_SOC = 80.0
DEFAULT_MINIMUM_POWER_FRACTION = 0.15
DEFAULT_DELIVERY_VARIATION = 0.04


@dataclass(frozen=True)
class BatterySession:
    """Describe one vehicle battery request independently from OCPP transport."""

    battery_kwh: float = DEFAULT_BATTERY_KWH
    start_soc: float = 0.0
    target_soc: float = 100.0
    unplug_soc: float | None = None
    max_power_w: float = DEFAULT_MAX_POWER_W
    meter_interval_seconds: float = 30.0
    taper_start_soc: float = DEFAULT_TAPER_START_SOC
    minimum_power_fraction: float = DEFAULT_MINIMUM_POWER_FRACTION
    delivery_variation: float = DEFAULT_DELIVERY_VARIATION
    line_voltage_v: float = DEFAULT_LINE_VOLTAGE_V
    seed: int = 0

    def validate(self) -> None:
        if self.battery_kwh <= 0:
            raise ValueError("battery_kwh must be positive")
        if not 0 <= self.start_soc < self.target_soc <= 100:
            raise ValueError("SOC must satisfy 0 <= start_soc < target_soc <= 100")
        if self.unplug_soc is not None and not (
            self.start_soc < self.unplug_soc <= self.target_soc
        ):
            raise ValueError(
                "unplug_soc must be greater than start_soc and no greater than target_soc"
            )
        if self.max_power_w <= 0:
            raise ValueError("max_power_w must be positive")
        if self.meter_interval_seconds <= 0:
            raise ValueError("meter_interval_seconds must be positive")
        if not 0 <= self.taper_start_soc <= 100:
            raise ValueError("taper_start_soc must be between 0 and 100")
        if not 0 < self.minimum_power_fraction <= 1:
            raise ValueError("minimum_power_fraction must be in (0, 1]")
        if not 0 <= self.delivery_variation < 1:
            raise ValueError("delivery_variation must be in [0, 1)")
        if self.line_voltage_v <= 0:
            raise ValueError("line_voltage_v must be positive")

    @property
    def effective_stop_soc(self) -> float:
        return self.unplug_soc if self.unplug_soc is not None else self.target_soc

    @property
    def requested_wh(self) -> float:
        return self.battery_kwh * 1000.0 * (
            self.effective_stop_soc - self.start_soc
        ) / 100.0


@dataclass(frozen=True)
class MeterChunk:
    index: int
    elapsed_seconds: float
    delivered_wh: float
    cumulative_delivered_wh: float
    state_of_charge: float
    power_w: float
    current_a: float
    voltage_v: float
    completed: bool


class BatteryDeliveryModel:
    """Generate reproducible, below-nominal meter chunks until demand is satisfied."""

    def __init__(self, session: BatterySession):
        session.validate()
        self.session = session
        self.delivered_wh = 0.0
        self.index = 0

    @property
    def completed(self) -> bool:
        return self.delivered_wh >= self.session.requested_wh - 1e-9

    @property
    def state_of_charge(self) -> float:
        added = self.delivered_wh / (self.session.battery_kwh * 1000.0) * 100.0
        return min(self.session.effective_stop_soc, self.session.start_soc + added)

    def _unit(self, label: str, index: int) -> float:
        digest = hashlib.sha256(
            f"{self.session.seed}:{label}:{index}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64 - 1)

    def _delivery_factor(self) -> float:
        """Bias bounded variation toward nominal while remaining strictly below it."""
        lower = max(0.0, 1.0 - self.session.delivery_variation)
        upper = 0.995
        if lower >= upper:
            return upper
        unit = self._unit("delivery", self.index)
        biased = unit**0.35
        return lower + (upper - lower) * biased

    def _taper_factor(self, soc: float) -> float:
        start = self.session.taper_start_soc
        if soc <= start:
            return 1.0
        span = max(1e-9, 100.0 - start)
        progress = min(1.0, max(0.0, (soc - start) / span))
        return self.session.minimum_power_fraction + (
            1.0 - self.session.minimum_power_fraction
        ) * (1.0 - progress) ** 2

    def _voltage(self) -> float:
        # EVSE-side line voltage remains nearly flat; small deterministic supply
        # variation is independent of battery SOC.
        unit = self._unit("voltage", self.index)
        return self.session.line_voltage_v * (0.995 + 0.01 * unit)

    def next_chunk(self) -> MeterChunk:
        if self.completed:
            raise StopIteration("battery session demand has already been satisfied")

        self.index += 1
        soc = self.state_of_charge
        power_w = (
            self.session.max_power_w
            * self._delivery_factor()
            * self._taper_factor(soc)
        )
        voltage_v = self._voltage()
        current_a = power_w / voltage_v
        ideal_wh = power_w * self.session.meter_interval_seconds / 3600.0
        remaining = self.session.requested_wh - self.delivered_wh
        delivered = min(ideal_wh, remaining)
        self.delivered_wh += delivered
        new_soc = self.state_of_charge
        return MeterChunk(
            index=self.index,
            elapsed_seconds=self.session.meter_interval_seconds,
            delivered_wh=delivered,
            cumulative_delivered_wh=self.delivered_wh,
            state_of_charge=new_soc,
            power_w=power_w,
            current_a=current_a,
            voltage_v=voltage_v,
            completed=self.completed,
        )
