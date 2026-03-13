import json
from pathlib import Path
from unittest.mock import ANY, patch

import pytest
from django.core.management import call_command


def test_ocpp_coverage_201_matches_fixture(tmp_path):
    output_path = tmp_path / "ocpp201_coverage.json"
    badge_path = tmp_path / "ocpp201_coverage.svg"

    call_command(
        "ocpp",
        "coverage",
        "--version",
        "2.0.1",
        json_path=output_path,
        badge_path=badge_path,
    )

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage201.json"
    expected = json.loads(fixture_path.read_text())
    assert generated["coverage"] == expected["coverage"]


def test_ocpp_coverage_16_routes_through_unified_command(tmp_path):
    """Regression: ``ocpp coverage`` should support the 1.6 coverage workflow."""

    output_path = tmp_path / "ocpp16_coverage.json"
    badge_path = tmp_path / "ocpp16_coverage.svg"

    call_command(
        "ocpp",
        "coverage",
        "--version",
        "1.6",
        json_path=output_path,
        badge_path=badge_path,
    )

    assert output_path.exists()
    assert badge_path.exists()


def test_ocpp_coverage_16_matches_fixture(tmp_path):
    """Regression: OCPP 1.6 coverage output stays aligned with checked-in fixture."""

    output_path = tmp_path / "ocpp16_coverage.json"
    badge_path = tmp_path / "ocpp16_coverage.svg"

    call_command(
        "ocpp",
        "coverage",
        "--version",
        "1.6J",
        json_path=output_path,
        badge_path=badge_path,
    )

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage.json"
    expected = json.loads(fixture_path.read_text())
    assert generated["coverage"] == expected["coverage"]
    assert generated["implemented"] == expected["implemented"]
    assert generated["spec"] == expected["spec"]


def test_ocpp_coverage_21_matches_fixture(tmp_path):
    """Regression: OCPP 2.1 coverage output stays aligned with checked-in fixture."""

    output_path = tmp_path / "ocpp21_coverage.json"
    badge_path = tmp_path / "ocpp21_coverage.svg"

    call_command(
        "ocpp",
        "coverage",
        "--version",
        "2.1",
        json_path=output_path,
        badge_path=badge_path,
    )

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage21.json"
    expected = json.loads(fixture_path.read_text())
    assert generated["coverage"] == expected["coverage"]
    assert generated["implemented"] == expected["implemented"]
    assert generated["spec"] == expected["spec"]
