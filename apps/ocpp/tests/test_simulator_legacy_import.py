"""Regression tests for the legacy ``apps.ocpp.simulator`` import path."""

from apps.ocpp.simulator import ChargePointSimulator, SimulatorConfig
from apps.simulators import ChargePointSimulator as CanonicalChargePointSimulator
from apps.simulators import SimulatorConfig as CanonicalSimulatorConfig


def test_legacy_simulator_import_path_re_exports_canonical_types():
    assert ChargePointSimulator is CanonicalChargePointSimulator
    assert SimulatorConfig is CanonicalSimulatorConfig
