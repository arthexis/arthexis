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


@pytest.mark.filterwarnings("default")
def test_legacy_coverage_command_routes_to_ocpp():
    with pytest.warns(UserWarning, match="coverage_ocpp16"):
        with patch("django.core.management.call_command") as mocked:
            call_command("coverage_ocpp16")

    mocked.assert_called_once_with(
        "ocpp",
        "coverage",
        "--version",
        "1.6",
        stdout=ANY,
        stderr=ANY,
    )
