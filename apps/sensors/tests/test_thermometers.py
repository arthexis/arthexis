from __future__ import annotations

from decimal import Decimal

from apps.sensors import thermometers


def test_read_i2c_temperature_empty_paths_skips_global_discovery(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.sensors.thermometers._iter_i2c_paths",
        lambda: ["/sys/class/hwmon/hwmon0/temp1_input"],
    )

    assert thermometers.read_i2c_temperature(paths=[]) is None


def test_read_temperature_auto_without_i2c_paths_reads_w1_only(monkeypatch) -> None:
    captured: dict[str, object] = {"i2c_called": False, "w1_paths": None}

    def fake_read_i2c(paths=None):
        captured["i2c_called"] = True
        return Decimal("30.1")

    def fake_read_w1(paths=None):
        captured["w1_paths"] = paths
        return Decimal("19.7")

    monkeypatch.setattr("apps.sensors.thermometers.read_i2c_temperature", fake_read_i2c)
    monkeypatch.setattr("apps.sensors.thermometers.read_w1_temperature", fake_read_w1)

    result = thermometers.read_temperature(
        source="auto",
        w1_paths=["/sys/bus/w1/devices/28-1/temperature"],
        i2c_paths=None,
    )

    assert captured["i2c_called"] is False
    assert captured["w1_paths"] == ["/sys/bus/w1/devices/28-1/temperature"]
    assert result == Decimal("19.7")
