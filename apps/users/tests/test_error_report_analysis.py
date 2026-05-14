from __future__ import annotations

import json
from io import BytesIO, StringIO
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.users import error_report_analysis
from apps.users.error_report_analysis import (
    analyze_error_report_package,
    redact_analysis_payload,
    redact_sensitive_text,
)


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


def test_analyze_error_report_package_detects_unredacted_secret_values(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"logs/runtime.log": "AWS_SECRET_ACCESS_KEY=real-secret"})

    result = analyze_error_report_package(package_path)

    assert result["max_severity"] == "critical"
    assert any(f["category"] == "secret_exposure" for f in result["findings"])


@pytest.mark.parametrize(
    "redacted_value",
    [
        "<redacted>",
        "[REDACTED]",
        "***redacted***",
        "***",
    ],
)
def test_analyze_error_report_package_ignores_redacted_secret_values(tmp_path, redacted_value):
    package_path = tmp_path / "error-report.zip"
    _write_report(
        package_path,
        logs={"logs/runtime.log": f"AWS_SECRET_ACCESS_KEY={redacted_value}"},
    )

    result = analyze_error_report_package(package_path)

    assert result["findings"] == []
    assert result["max_severity"] == "none"


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


def test_analyze_error_report_package_scans_in_repo_log_text_paths(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"work/app-logs/server.txt": "Traceback (most recent call last):"})

    result = analyze_error_report_package(package_path)

    assert any(f["category"] == "startup" for f in result["findings"])


def test_analyze_error_report_package_scans_external_text_logs(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"external/tmp/log.txt": "Traceback (most recent call last):"})

    result = analyze_error_report_package(package_path)

    assert any(f["category"] == "startup" for f in result["findings"])


def test_analyze_error_report_package_scans_external_text_logs_case_insensitively(tmp_path):
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"external/tmp/error.TXT": "Traceback (most recent call last):"})

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


def test_analyze_error_report_package_rejects_large_summary(tmp_path):
    package_path = tmp_path / "error-report.zip"
    with ZipFile(package_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"warnings": [], "entries": []}))
        zf.writestr("summary.txt", "x" * (600 * 1024))

    with pytest.raises(ValueError, match="Malformed error-report package"):
        analyze_error_report_package(package_path)


def test_analyze_error_report_package_limits_log_entries_without_rejecting(monkeypatch, tmp_path):
    monkeypatch.setattr(error_report_analysis, "MAX_LOG_ENTRIES_SCANNED", 2)
    package_path = tmp_path / "error-report.zip"
    logs = {
        "logs/log-0.txt": "ok",
        "logs/log-1.txt": "ok",
        "logs/log-2.txt": "Traceback (most recent call last):",
    }
    _write_report(package_path, logs=logs)

    result = analyze_error_report_package(package_path)

    assert result["findings"] == []
    assert result["max_severity"] == "none"


def test_analyze_error_report_package_rejects_large_log_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(error_report_analysis, "MAX_LOG_ENTRY_BYTES", 5)
    monkeypatch.setattr(error_report_analysis, "MAX_TOTAL_LOG_BYTES", 20)
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"logs/runtime.log": "x" * 6})

    with pytest.raises(ValueError, match="Malformed error-report package"):
        analyze_error_report_package(package_path)


def test_analyze_error_report_package_rejects_log_bytes_over_total(monkeypatch, tmp_path):
    monkeypatch.setattr(error_report_analysis, "MAX_LOG_ENTRY_BYTES", 20)
    monkeypatch.setattr(error_report_analysis, "MAX_TOTAL_LOG_BYTES", 5)
    package_path = tmp_path / "error-report.zip"
    _write_report(package_path, logs={"logs/runtime.log": "x" * 6})

    with pytest.raises(ValueError, match="Malformed error-report package"):
        analyze_error_report_package(package_path)


def test_analyze_error_report_package_rejects_too_many_total_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(error_report_analysis, "MAX_TOTAL_ENTRIES", 3)
    package_path = tmp_path / "error-report.zip"
    with ZipFile(package_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"warnings": [], "entries": []}))
        zf.writestr("summary.txt", "ok")
        zf.writestr("attachments/one.bin", "ok")
        zf.writestr("attachments/two.bin", "ok")

    with pytest.raises(ValueError, match="Malformed error-report package"):
        analyze_error_report_package(package_path)


def test_iter_log_text_tracks_actual_bytes_not_zip_metadata(monkeypatch):
    class FakeZipInfo:
        def __init__(self, filename):
            self.filename = filename
            self.file_size = 0

    class FakeZipFile:
        def __init__(self):
            self.infos = [FakeZipInfo("logs/one.log"), FakeZipInfo("logs/two.log")]
            self.payloads = {
                "logs/one.log": b"12345",
                "logs/two.log": b"6",
            }

        def infolist(self):
            return self.infos

        def open(self, info):
            return BytesIO(self.payloads[info.filename])

    monkeypatch.setattr(error_report_analysis, "MAX_TOTAL_LOG_BYTES", 5)
    monkeypatch.setattr(error_report_analysis, "MAX_LOG_ENTRY_BYTES", 5)

    log_iter = error_report_analysis._iter_log_text(FakeZipFile())

    assert next(log_iter) == ("logs/one.log", "12345")
    with pytest.raises(ValueError, match="total scan budget"):
        next(log_iter)


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


def test_redact_analysis_payload_removes_sensitive_values():
    payload = {
        "package": "AWS_SECRET_ACCESS_KEY=real-secret/report.zip",
        "warnings": ["api_key=visible-token"],
        "findings": [
            {
                "severity": "critical",
                "category": "secret_exposure",
                "message": "password=hunter2",
                "source": "logs/runtime.log",
            }
        ],
    }

    redacted = redact_analysis_payload(payload)
    rendered = json.dumps(redacted, sort_keys=True)

    assert "real-secret" not in rendered
    assert "visible-token" not in rendered
    assert "hunter2" not in rendered
    assert "secret_exposure" in rendered
    assert "[redacted]" in rendered


def test_redact_sensitive_text_handles_pem_variants_and_quoted_values():
    private_key_label = "RSA " + "PRIVATE " + "KEY"
    text = (
        f"-----BEGIN {private_key_label}-----\n"
        "real-key-material\n"
        f"-----END {private_key_label}-----\n"
        "password=\"my secret password\"\n"
        "token='visible-token'\n"
        "{\"token\":\"abc\\\"def\"}\n"
        "token='abc\\''def'\n"
        r"token=backslash\trail" "\n"
        "client_secret=client-value\n"
        "refresh_token=refresh-value\n"
        "refresh_token=abc\\\"def\n"
        r"refresh_token=double\\\"escaped" "\n"
        "db_password=db-value\n"
        "api_key=bare-secret"
    )

    redacted = redact_sensitive_text(text)

    assert "real-key-material" not in redacted
    assert "my secret password" not in redacted
    assert "visible-token" not in redacted
    assert "abc" not in redacted
    assert "backslash" not in redacted
    assert "trail" not in redacted
    assert "double" not in redacted
    assert "escaped" not in redacted
    assert "client-value" not in redacted
    assert "refresh-value" not in redacted
    assert 'abc\\"def' not in redacted
    assert r'double\\\"escaped' not in redacted
    assert "db-value" not in redacted
    assert "bare-secret" not in redacted
    assert "[redacted private key]" in redacted
    assert 'password="[redacted]"' in redacted
    assert "token='[redacted]'" in redacted
    assert '{"token":"[redacted]"}' in redacted
    assert "client_secret=[redacted]" in redacted
    assert "refresh_token=[redacted]" in redacted
    assert "db_password=[redacted]" in redacted
    assert "api_key=[redacted]" in redacted


def test_redact_sensitive_text_redacts_unquoted_values_with_symbol_suffixes():
    text = "password=abc$def&ghi?jkl"

    redacted = redact_sensitive_text(text)

    assert redacted == "password=[redacted]"


def test_redact_sensitive_text_redacts_malformed_double_quoted_symbol_values():
    text = 'password="$ecret'

    redacted = redact_sensitive_text(text)

    assert redacted == "password=[redacted]"

def test_redact_sensitive_text_handles_unterminated_quoted_backslash_sequence():
    text = 'password="' + ('\\' * 64)

    redacted = redact_sensitive_text(text)

    assert redacted == 'password=[redacted]'

def test_redact_analysis_payload_preserves_tuple_type():
    redacted = redact_analysis_payload(("password=hunter2", "ok"))

    assert redacted == ("password=[redacted]", "ok")
    assert isinstance(redacted, tuple)


def test_diagnostics_analyze_redacts_json_stdout_and_output(monkeypatch, tmp_path):
    result = {
        "package": "AWS_SECRET_ACCESS_KEY=real-secret/report.zip",
        "entry_count": 1,
        "warnings": ["token=visible-token"],
        "findings": [
            {
                "severity": "critical",
                "category": "secret_exposure",
                "message": "password=hunter2",
                "source": "logs/runtime.log",
            }
        ],
        "max_severity": "critical",
        "max_severity_rank": 4,
        "risk_score": 41,
        "severity_order": {
            "none": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        },
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

    rendered_stdout = stdout.getvalue()
    rendered_file = output_path.read_text(encoding="utf-8")

    assert "real-secret" not in rendered_stdout
    assert "visible-token" not in rendered_stdout
    assert "hunter2" not in rendered_stdout
    assert "real-secret" not in rendered_file
    assert "visible-token" not in rendered_file
    assert "hunter2" not in rendered_file
    assert "secret_exposure" in rendered_file


def test_diagnostics_analyze_redacts_failure_message(monkeypatch):
    def raise_sensitive_error(_path):
        raise ValueError('password="my secret password"')

    monkeypatch.setattr(
        "apps.users.management.commands.diagnostics.analyze_error_report_package",
        raise_sensitive_error,
    )

    with pytest.raises(CommandError) as exc_info:
        call_command(
            "diagnostics",
            "analyze",
            package="AWS_SECRET_ACCESS_KEY=real-secret.zip",
        )

    message = str(exc_info.value)
    assert "real-secret" not in message
    assert "my secret password" not in message
    assert "[redacted]" in message


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
