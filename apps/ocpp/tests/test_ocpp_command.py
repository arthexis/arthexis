import json
from pathlib import Path

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
