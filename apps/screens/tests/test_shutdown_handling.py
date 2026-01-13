from __future__ import annotations

import signal

import pytest

from apps.screens import lcd_screen


def _signal(name: str, fallback: signal.Signals) -> signal.Signals:
    return getattr(signal, name, fallback)


@pytest.fixture(autouse=True)
def reset_shutdown_flag():
    lcd_screen._reset_shutdown_flag()
    lcd_screen._reset_event_interrupt_flag()
    yield
    lcd_screen._reset_shutdown_flag()
    lcd_screen._reset_event_interrupt_flag()


def test_shutdown_flag_can_be_reset_and_requested():
    assert lcd_screen._shutdown_requested() is False

    lcd_screen._request_shutdown(signal.SIGTERM, None)

    assert lcd_screen._shutdown_requested() is True


def test_handle_shutdown_blanks_display_when_flag_set():
    class FakeLCD:
        def __init__(self) -> None:
            self.cleared = False
            self.writes: list[tuple[int, int, str]] = []

        def clear(self) -> None:
            self.cleared = True

        def write(self, col: int, row: int, text: str) -> None:
            self.writes.append((col, row, text))

    lcd = FakeLCD()
    lcd_screen._request_shutdown(signal.SIGINT, None)

    should_exit = lcd_screen._handle_shutdown_request(lcd)

    assert should_exit is True
    assert lcd.cleared is True
    blank_row = " " * lcd_screen.LCD_COLUMNS
    assert lcd.writes == [(0, row, blank_row) for row in range(lcd_screen.LCD_ROWS)]


def test_handle_shutdown_with_no_lcd_is_quiet():
    lcd_screen._request_shutdown(_signal("SIGHUP", signal.SIGTERM), None)

    should_exit = lcd_screen._handle_shutdown_request(None)

    assert should_exit is True


def test_blank_display_swallows_lcd_errors(monkeypatch):
    class FailingLCD:
        def clear(self) -> None:  # pragma: no cover - exercised via _blank_display
            raise RuntimeError("clear boom")

        def write(self, *args, **kwargs) -> None:  # pragma: no cover - exercised via _blank_display
            raise RuntimeError("write boom")

    lcd_screen._request_shutdown(signal.SIGTERM, None)

    should_exit = lcd_screen._handle_shutdown_request(FailingLCD())

    assert should_exit is True


def test_event_interrupt_flag_can_be_requested_and_reset():
    assert lcd_screen._event_interrupt_requested() is False

    lcd_screen._request_event_interrupt(_signal("SIGUSR1", signal.SIGTERM), None)

    assert lcd_screen._event_interrupt_requested() is True

    lcd_screen._reset_event_interrupt_flag()

    assert lcd_screen._event_interrupt_requested() is False
