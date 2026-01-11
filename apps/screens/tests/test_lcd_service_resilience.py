from __future__ import annotations

import builtins
import importlib.util
import os
import sys
import time
from pathlib import Path

from apps.screens import lcd_screen


def test_base_dir_resolution_avoids_django(monkeypatch, tmp_path):
    module_name = "lcd_screen_test_instance"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(lcd_screen.__file__).resolve()
    )
    assert spec and spec.loader

    def guarded_import(name, *args, **kwargs):
        if name.startswith("django"):
            raise AssertionError("django should not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv("ARTHEXIS_BASE_DIR", str(tmp_path))
    original_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[arg-type]
    finally:
        sys.modules.pop(module_name, None)

    assert module.BASE_DIR == tmp_path


def test_frame_writer_writes_fallback_file_on_failure(tmp_path):
    class FailingLCD:
        def write_frame(self, *args, **kwargs):
            raise RuntimeError("boom")

    work_file = tmp_path / "lcd-screen.txt"
    writer = lcd_screen.LCDFrameWriter(FailingLCD(), work_file=work_file)

    success = writer.write("hello", "world")

    assert success is False
    contents = work_file.read_text(encoding="utf-8").splitlines()
    assert contents == ["hello".ljust(lcd_screen.LCD_COLUMNS), "world".ljust(lcd_screen.LCD_COLUMNS)]


def test_low_lock_file_cleared_on_startup_when_stale_and_empty(tmp_path):
    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("\n", encoding="utf-8")

    old_timestamp = time.time() - (2 * 3600)
    os.utime(lock_file, (old_timestamp, old_timestamp))

    lcd_screen._clear_low_lock_file(lock_file=lock_file, stale_after_seconds=3600)

    assert not lock_file.exists()


def test_low_lock_file_kept_when_stale_but_populated(tmp_path):
    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("UP 0d0h0m\nON 0m0s\n", encoding="utf-8")

    old_timestamp = time.time() - (2 * 3600)
    os.utime(lock_file, (old_timestamp, old_timestamp))

    lcd_screen._clear_low_lock_file(lock_file=lock_file, stale_after_seconds=3600)

    assert lock_file.exists()


def test_low_lock_file_kept_when_recent(tmp_path):
    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("data", encoding="utf-8")

    lcd_screen._clear_low_lock_file(lock_file=lock_file, stale_after_seconds=3600)

    assert lock_file.exists()
