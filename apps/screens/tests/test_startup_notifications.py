from __future__ import annotations

from pathlib import Path

from apps.screens import startup_notifications


def test_iter_lcd_channel_lock_files_orders_numbers(tmp_path: Path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    for name in ("lcd-low-3.lck", "lcd-low-1.lck", "lcd-low.lck"):
        (lock_dir / name).write_text("payload", encoding="utf-8")

    ordered = startup_notifications.iter_lcd_channel_lock_files(lock_dir, "low")

    assert [path.name for path in ordered] == [
        "lcd-low.lck",
        "lcd-low-1.lck",
        "lcd-low-3.lck",
    ]


def test_lcd_feature_enabled_accepts_numbered_lock(tmp_path: Path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lcd-high-5.lck").write_text("payload", encoding="utf-8")

    assert startup_notifications.lcd_feature_enabled(lock_dir)
