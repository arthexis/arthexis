from __future__ import annotations

from datetime import datetime
import random

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

    assert line1 == "2024-01-01 Mon01"
    assert len(line1) == 16
    assert line2.endswith(expected_suffix)


@pytest.mark.parametrize(
    "temperature_label,use_fahrenheit,expected_suffix",
    [
        ("100.0C", False, "100C"),
        ("38.0C", True, "100F"),
    ],
)
def test_clock_payload_omits_decimals_for_three_digits(
    monkeypatch, temperature_label, use_fahrenheit, expected_suffix
):
    monkeypatch.setattr(lcd_screen, "_lcd_temperature_label", lambda: temperature_label)

    _, line2, _, _ = lcd_screen._clock_payload(
        datetime(2024, 1, 1, 12, 0), use_fahrenheit=use_fahrenheit
    )

    assert line2.endswith(expected_suffix)


def test_clock_payload_can_use_fate_vector(monkeypatch):
    deck = lcd_screen.FateDeck(rng=random.Random(0))
    monkeypatch.setattr(lcd_screen, "FATE_VECTOR", "")

    line1, line2, _, _ = lcd_screen._clock_payload(
        datetime(2024, 1, 1, 3, 15),
        fate_deck=deck,
        choose_fate=lambda: True,
    )

    assert line1.startswith("2024-01-01")
    prefix = line2.split()[0]

    assert prefix == lcd_screen.FATE_VECTOR
    assert line2.startswith(f"{prefix} 03:15")


def test_clock_payload_respects_standard_am_pm(monkeypatch):
    deck = lcd_screen.FateDeck(rng=random.Random(0))
    lcd_screen.FATE_VECTOR = "PREVIOUS"

    _, line2, _, _ = lcd_screen._clock_payload(
        datetime(2024, 1, 1, 3, 15),
        fate_deck=deck,
        choose_fate=lambda: False,
    )

    assert line2.startswith("AM 03:15")
    assert lcd_screen.FATE_VECTOR == "PREVIOUS"


def test_fate_deck_reshuffles_when_empty():
    deck = lcd_screen.FateDeck(rng=random.Random(1))
    drawn = [deck.draw() for _ in range(55)]

    next_card = deck.draw()

    assert len(set(drawn)) == 55
    assert "YY" in set(drawn)
    assert deck.remaining == 54
    assert next_card
