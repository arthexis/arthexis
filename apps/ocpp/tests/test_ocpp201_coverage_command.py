import json
from pathlib import Path

from django.core.management import call_command


def test_ocpp201_coverage_matches_fixture(tmp_path):
    output_path = tmp_path / "ocpp201_coverage.json"
    badge_path = tmp_path / "ocpp201_coverage.svg"
    call_command("ocpp201_coverage", json_path=output_path, badge_path=badge_path)

    assert output_path.exists(), "Expected coverage summary to be written"

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage21.json"
    expected = json.loads(fixture_path.read_text())

    assert generated["coverage"] == expected["coverage"]
    assert generated["implemented"] == expected["implemented"]
    assert generated["spec"] == expected["spec"]
