"""Backward-compatible wrapper for the legacy OCPP simulator import path."""

from apps.simulators import ChargePointSimulator, SimulatorConfig

__all__ = ["ChargePointSimulator", "SimulatorConfig"]
