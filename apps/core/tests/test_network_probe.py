"""Tests for system UI network probing behaviors."""

from __future__ import annotations

import pytest

from apps.core.system.ui import network_probe
from utils.service_probe import ServiceProbeResult


def test_detect_runserver_process_uses_shared_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runserver process detection should delegate to the shared service probe helper."""

    monkeypatch.setattr(network_probe, "detect_runserver_port", lambda: 7777)

    running, port = network_probe._detect_runserver_process()
    assert running is True
    assert port == 7777


def test_probe_ports_uses_http_probe_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Port probing should return the first port where admin HTTP probing succeeds."""

    results = {
        8888: ServiceProbeResult(reachable=False, status_code=None),
        8000: ServiceProbeResult(reachable=True, status_code=200),
    }

    def fake_probe(port: int, *, timeout: float = 0.25) -> ServiceProbeResult:
        assert timeout == 0.25
        return results[port]

    monkeypatch.setattr(network_probe, "probe_admin_login", fake_probe)

    reachable, port = network_probe._probe_ports([8888, 8000])
    assert reachable is True
    assert port == 8000
