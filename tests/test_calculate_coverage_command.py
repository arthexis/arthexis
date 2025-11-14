from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from utils.coverage import CoverageSummary, coverage_color, render_badge


def _write_report(path: Path, *, covered: int, total: int, missing: int | None = None) -> None:
    payload = {
        "meta": {"version": "1"},
        "totals": {
            "covered_lines": covered,
            "missing_lines": missing if missing is not None else max(total - covered, 0),
            "num_statements": total,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_calculate_coverage_command_generates_badge(tmp_path):
    report = tmp_path / "coverage.json"
    badge = tmp_path / "badges" / "coverage.svg"
    _write_report(report, covered=82, total=100)

    stdout = io.StringIO()
    call_command(
        "calculate_coverage",
        "--coverage-json",
        str(report),
        "--badge-path",
        str(badge),
        "--label",
        "python",
        stdout=stdout,
    )

    summary = json.loads(stdout.getvalue())
    assert summary["covered_lines"] == 82
    assert summary["percent_covered"] == pytest.approx(82.0)

    svg_text = badge.read_text(encoding="utf-8")
    assert "<svg" in svg_text
    assert "python" in svg_text
    assert "82.0%" in svg_text
    assert "#97CA00" in svg_text  # 82% falls into the green threshold


def test_calculate_coverage_rejects_invalid_json(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text("not json", encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("calculate_coverage", "--coverage-json", str(report))


def test_calculate_coverage_requires_totals(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"covered_lines": 5}}), encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("calculate_coverage", "--coverage-json", str(report))


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        (95.0, "#4c1"),
        (80.0, "#97CA00"),
        (65.0, "#dfb317"),
        (45.0, "#fe7d37"),
        (10.0, "#e05d44"),
    ],
)
def test_coverage_color_thresholds(percentage: float, expected: str):
    assert coverage_color(percentage) == expected


def test_summary_handles_empty_suite():
    payload = {"totals": {"covered_lines": 0, "missing_lines": 0, "num_statements": 0}}
    summary = CoverageSummary.from_payload(payload)
    assert summary.percent == 100.0
    assert summary.to_dict()["percent_covered"] == 100.0


def test_render_badge_includes_label_and_value():
    svg = render_badge("unit", "75.5%", "#4c1")
    assert "unit" in svg
    assert "75.5%" in svg
    assert "#4c1" in svg
