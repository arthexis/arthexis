import json
from datetime import datetime, timezone as datetime_timezone

from apps.screens import lcd_screen


def test_simulator_lock_payload_clears_when_sim_stopped(
    tmp_path, monkeypatch
) -> None:
    state_file = tmp_path / "apps" / "ocpp" / "simulator.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"1": {"running": False}}), encoding="utf-8")

    monkeypatch.setattr(lcd_screen, "SIMULATOR_STATE_FILE", state_file)
    lcd_screen._SIMULATOR_RUNNING_CACHE.update(
        {"checked_at": 0.0, "is_running": False}
    )

    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("SIM CP1\nRunning\n", encoding="utf-8")

    payload = lcd_screen._read_lock_payload(
        lock_file, now=datetime.now(datetime_timezone.utc)
    )

    assert payload is None
    assert not lock_file.exists()


def test_simulator_lock_payload_kept_when_sim_running(
    tmp_path, monkeypatch
) -> None:
    state_file = tmp_path / "apps" / "ocpp" / "simulator.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"1": {"running": True}}), encoding="utf-8")

    monkeypatch.setattr(lcd_screen, "SIMULATOR_STATE_FILE", state_file)
    lcd_screen._SIMULATOR_RUNNING_CACHE.update(
        {"checked_at": 0.0, "is_running": False}
    )

    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("SIM CP1\nRunning\n", encoding="utf-8")

    payload = lcd_screen._read_lock_payload(
        lock_file, now=datetime.now(datetime_timezone.utc)
    )

    assert payload is not None
    assert lock_file.exists()
