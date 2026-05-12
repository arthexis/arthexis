from __future__ import annotations

import json
from io import StringIO
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.users.error_report_analysis import analyze_error_report_package


def _write_report(path, *, summary="", logs=None, warnings=None):
    logs = logs or {}
    warnings = warnings or []
    with ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"warnings": warnings, "entries": list(logs.keys())}))
        zf.writestr("summary.txt", summary)
        for name, text in logs.items():
            zf.writestr(name, text)


def test_analyze_error_report_package_detects_high_severity(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(
        package_path,
        summary="Traceback (most recent call last):",
        logs={"logs/runtime.log": "django.db.utils.OperationalError"},
        warnings=["test warning"],
    )

    result = analyze_error_report_package(package_path)

    assert result["max_severity"] == "high"
    assert result["warnings"] == ["test warning"]
    assert any(f["category"] == "migration" for f in result["findings"])


def test_analyze_error_report_package_handles_empty_findings(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, summary="all good", logs={"logs/runtime.log": "ok"})

    result = analyze_error_report_package(package_path)

    assert result["findings"] == []
    assert result["max_severity"] == "none"
    assert result["max_severity_rank"] == 0
    assert result["risk_score"] == 0


def test_analyze_error_report_package_scans_top_level_logs_directory(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"logs/startup.txt": "Traceback (most recent call last):"})

    result = analyze_error_report_package(package_path)

    assert any(f["category"] == "startup" for f in result["findings"])




def test_analyze_error_report_package_scans_external_text_logs(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"external/tmp/log.txt": "Traceback (most recent call last):"})

    result = analyze_error_report_package(package_path)

    assert any(f["category"] == "startup" for f in result["findings"])


@pytest.mark.parametrize(
    "manifest",
    [
        {"warnings": "abc", "entries": []},
        {"warnings": [], "entries": "abc"},
        {"warnings": [1], "entries": []},
    ],
)
def test_analyze_error_report_package_rejects_malformed_manifest_lists(tmp_path, manifest):
    package_path = tmp_path / "error-report.zip"
    with ZipFile(package_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("summary.txt", "ok")

    with pytest.raises(ValueError, match="Malformed error-report package"):
        analyze_error_report_package(package_path)


def test_diagnostics_analyze_requires_package():
    with pytest.raises(CommandError, match="--package is required"):
        call_command("diagnostics", "analyze")


def test_diagnostics_analyze_json_output_and_write_file(monkeypatch, tmp_path):
    result = {
        "package": "x.zip",
        "entry_count": 1,
        "warnings": [],
        "findings": [{"severity": "medium", "category": "service", "message": "m", "source": "s"}],
        "max_severity": "medium",
        "max_severity_rank": 2,
        "risk_score": 22,
        "severity_order": {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4},
    }
    monkeypatch.setattr(
        "apps.users.management.commands.diagnostics.analyze_error_report_package",
        lambda _path: result,
    )
    output_path = tmp_path / "analysis" / "result.json"
    stdout = StringIO()

    call_command(
        "diagnostics",
        "analyze",
        package="fake.zip",
        format="json",
        output=str(output_path),
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["risk_score"] == 22
    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["findings"]


def test_diagnostics_analyze_fail_on_threshold(monkeypatch):
    monkeypatch.setattr(
        "apps.users.management.commands.diagnostics.analyze_error_report_package",
        lambda _path: {
            "package": "x.zip",
            "entry_count": 0,
            "warnings": [],
            "findings": [],
            "max_severity": "high",
            "max_severity_rank": 3,
            "risk_score": 30,
            "severity_order": {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4},
        },
    )

    with pytest.raises(CommandError, match="threshold reached"):
        call_command("diagnostics", "analyze", package="fake.zip", fail_on="medium")


def test_diagnostics_analyze_fail_on_low_allows_clean_report(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, summary="all good", logs={"logs/runtime.log": "ok"})
    stdout = StringIO()

    call_command(
        "diagnostics",
        "analyze",
        package=str(package_path),
        fail_on="low",
        stdout=stdout,
    )

    assert "Risk score: 0 (none)" in stdout.getvalue()
