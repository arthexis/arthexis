from __future__ import annotations

import builtins
import importlib.util
import sys
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


def test_scroll_interval_defaults_when_state_cleared(monkeypatch):
    class FailingLCD:
        def write_frame(self, *args, **kwargs):
            raise RuntimeError("boom")

    display_state = lcd_screen._prepare_display_state(
        "hello world" * 2, "", lcd_screen.DEFAULT_SCROLL_MS
    )
    writer = lcd_screen.LCDFrameWriter(FailingLCD())
    scheduler = lcd_screen.ScrollScheduler()
    health = lcd_screen.LCDHealthMonitor(base_delay=0.0, max_delay=0.0)

    advanced: list[float] = []
    monkeypatch.setattr(scheduler, "advance", lambda interval: advanced.append(interval))
    monkeypatch.setattr(lcd_screen.time, "sleep", lambda delay: None)

    scheduler.sleep_until_ready()
    display_state, write_success = lcd_screen._advance_display(display_state, writer)
    assert write_success is False

    lcd = object()
    next_display_state = object()
    if lcd is not None and writer.lcd is None:
        lcd = None
        writer = lcd_screen.LCDFrameWriter(None)
        display_state = None
        next_display_state = None
    health.record_failure()

    scheduler.advance(display_state.scroll_sec if display_state else 0.5)

    assert advanced == [0.5]
    assert lcd is None
    assert next_display_state is None
