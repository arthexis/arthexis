from __future__ import annotations

import json
from zipfile import ZipFile

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
    assert result["max_severity"] == "low"
