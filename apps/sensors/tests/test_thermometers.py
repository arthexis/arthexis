from __future__ import annotations

from decimal import Decimal

from apps.sensors import thermometers


def test_read_i2c_temperature_empty_paths_skips_discovery(monkeypatch) -> None:
    monkeypatch.setattr(
        thermometers,
        "_iter_i2c_paths",
        lambda: ["/sys/class/hwmon/hwmon0/temp1_input"],
    )

    assert thermometers.read_i2c_temperature([]) is None


def test_read_temperature_auto_uses_w1_without_i2c_paths(monkeypatch) -> None:
    calls: list[str] = []

    def fake_read_w1_temperature(paths):
        calls.append("w1")
        return Decimal("21.5")

    def fake_read_i2c_temperature(paths):
        calls.append("i2c")
        return Decimal("25.0")

    monkeypatch.setattr(thermometers, "read_w1_temperature", fake_read_w1_temperature)
    monkeypatch.setattr(thermometers, "read_i2c_temperature", fake_read_i2c_temperature)

    reading = thermometers.read_temperature(source="auto", w1_paths=["/tmp/w1"])

    assert reading == Decimal("21.5")
    assert calls == ["w1"]
