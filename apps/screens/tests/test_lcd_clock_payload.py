from __future__ import annotations

from datetime import datetime

import pytest

from apps.screens import lcd_screen


@pytest.mark.parametrize(
    "use_fahrenheit,expected_suffix",
    [
        (False, "10.0C"),
        (True, "50.0F"),
    ],
)
def test_clock_payload_formats_temperature_units(monkeypatch, use_fahrenheit, expected_suffix):
    monkeypatch.setattr(lcd_screen, "_lcd_temperature_label", lambda: "10.0C")

    line1, line2, _, _ = lcd_screen._clock_payload(
        datetime(2024, 1, 1, 12, 0), use_fahrenheit=use_fahrenheit
    )

    assert line1 == "2024-01-01 Mon"
    assert line2.endswith(expected_suffix)
