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


def test_read_soc_temperature_uses_cpu_thermal_zone(tmp_path) -> None:
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("cpu-thermal\n", encoding="utf-8")
    temp_path = zone / "temp"
    temp_path.write_text("72062\n", encoding="utf-8")

    assert thermometers.read_soc_temperature(thermal_zone_paths=[temp_path]) == Decimal(
        "72.062"
    )


def test_read_soc_temperature_uses_cpu_hwmon_name(tmp_path) -> None:
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("cpu_thermal\n", encoding="utf-8")
    temp_path = hwmon / "temp1_input"
    temp_path.write_text("69600\n", encoding="utf-8")

    assert thermometers.read_soc_temperature(
        thermal_zone_paths=[],
        hwmon_paths=[temp_path],
    ) == Decimal("69.6")


def test_read_soc_temperature_missing_source_returns_none(tmp_path) -> None:
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("acpitz\n", encoding="utf-8")
    temp_path = zone / "temp"
    temp_path.write_text("42000\n", encoding="utf-8")

    assert (
        thermometers.read_soc_temperature(
            thermal_zone_paths=[temp_path],
            hwmon_paths=[],
        )
        is None
    )
