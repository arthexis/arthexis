import json
from pathlib import Path

from django.core.management import call_command

from apps.ocpp.management.commands.coverage_ocpp16 import _implemented_cp_to_csms


def test_notify_display_messages_in_cp_to_csms_coverage():
    app_dir = Path(__file__).resolve().parents[1]
    implemented = _implemented_cp_to_csms(app_dir)

    assert "NotifyDisplayMessages" in implemented


def test_ocpp21_coverage_matches_fixture(tmp_path):
    output_path = tmp_path / "ocpp21_coverage.json"
    badge_path = tmp_path / "ocpp21_coverage.svg"
    call_command("coverage_ocpp21", json_path=output_path, badge_path=badge_path)

    assert output_path.exists(), "Expected coverage summary to be written"

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage21.json"
    expected = json.loads(fixture_path.read_text())

    assert generated["coverage"] == expected["coverage"]
    assert generated["implemented"] == expected["implemented"]
    assert generated["spec"] == expected["spec"]


def test_ocpp201_coverage_matches_fixture(tmp_path):
    output_path = tmp_path / "ocpp201_coverage.json"
    badge_path = tmp_path / "ocpp201_coverage.svg"
    call_command("coverage_ocpp201", json_path=output_path, badge_path=badge_path)

    assert output_path.exists(), "Expected coverage summary to be written"

    generated = json.loads(output_path.read_text())
    fixture_path = Path(__file__).resolve().parents[1] / "coverage201.json"
    expected = json.loads(fixture_path.read_text())

    assert generated["coverage"] == expected["coverage"]
    assert generated["implemented"] == expected["implemented"]
    assert generated["spec"] == expected["spec"]
