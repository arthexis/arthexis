from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import URLError

import pytest

from scripts import error_report


@pytest.fixture(autouse=True)
def clear_arthexis_log_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARTHEXIS_LOG_DIR", raising=False)


def test_build_report_redacts_text_and_excludes_sensitive_files(tmp_path: Path) -> None:
    base_dir = tmp_path
    (base_dir / "logs").mkdir()
    (base_dir / ".locks").mkdir()
    (base_dir / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (base_dir / "arthexis.env").write_text("SECRET_KEY=do-not-copy\n", encoding="utf-8")
    (base_dir / "db.sqlite3").write_text("database", encoding="utf-8")
    (base_dir / ".locks" / "service.lck").write_text("arthexis\n", encoding="utf-8")
    (base_dir / "logs" / "error.log").write_text(
        "SECRET_KEY=do-not-leak\nAuthorization: Bearer abc.def.ghi\n",
        encoding="utf-8",
    )

    result = error_report.build_report(
        error_report.ReportConfig(
            base_dir=base_dir,
            output_dir=base_dir / "work" / "error-reports",
        )
    )

    assert result.path.exists()
    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "summary.txt" in names
        assert "logs/error.log" in names
        assert "arthexis/locks/service.lck" in names
        assert "arthexis.env" not in names
        assert "db.sqlite3" not in names
        payload = archive.read("logs/error.log").decode("utf-8")

    assert "do-not-leak" not in payload
    assert "abc.def.ghi" not in payload
    assert "SECRET_KEY=<redacted>" in payload
    assert "Bearer <redacted>" in payload


def test_build_report_excludes_sensitive_rfid_artifacts(tmp_path: Path) -> None:
    base_dir = tmp_path
    (base_dir / "logs").mkdir()
    (base_dir / ".locks").mkdir()
    log_artifact = base_dir / "logs" / "RFID-SCANS.NDJSON"
    lock_artifact = base_dir / ".locks" / "RFID-SCAN.JSON"
    log_artifact.write_text('{"rfid":"123","keys":"secret"}\n', encoding="utf-8")
    lock_artifact.write_text('{"rfid":"123","dump":"secret"}\n', encoding="utf-8")

    result = error_report.build_report(
        error_report.ReportConfig(
            base_dir=base_dir,
            output_dir=base_dir / "work" / "error-reports",
        )
    )

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())

    assert error_report.is_sensitive_path(log_artifact, base_dir=base_dir) is True
    assert error_report.is_sensitive_path(lock_artifact, base_dir=base_dir) is True
    assert "logs/RFID-SCANS.NDJSON" not in names
    assert "arthexis/locks/RFID-SCAN.JSON" not in names


def test_build_report_writes_manifest_with_entry_hashes(tmp_path: Path) -> None:
    base_dir = tmp_path
    (base_dir / "logs").mkdir()
    (base_dir / "logs" / "error.log").write_text("boom\n", encoding="utf-8")

    result = error_report.build_report(
        error_report.ReportConfig(
            base_dir=base_dir,
            output_dir=base_dir / "reports",
            max_log_files=1,
            max_file_bytes=128,
        )
    )

    with zipfile.ZipFile(result.path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest["schema_version"] == error_report.SCHEMA_VERSION
    assert manifest["options"]["max_log_files"] == 1
    assert manifest["options"]["max_file_bytes"] == 128
    assert any(entry["path"] == "logs/error.log" for entry in manifest["entries"])
    assert all(entry["sha256"] for entry in manifest["entries"])


def test_dry_run_returns_planned_entries_without_writing_zip(tmp_path: Path) -> None:
    base_dir = tmp_path
    (base_dir / "logs").mkdir()
    (base_dir / "logs" / "error.log").write_text("boom\n", encoding="utf-8")
    output_dir = base_dir / "reports"

    result = error_report.build_report(
        error_report.ReportConfig(
            base_dir=base_dir,
            output_dir=output_dir,
            dry_run=True,
        )
    )

    assert result.dry_run is True
    assert not result.path.exists()
    assert any(entry.archive_path == "logs/error.log" for entry in result.entries)


def test_upload_report_requires_https_by_default(tmp_path: Path) -> None:
    report_path = tmp_path / "report.zip"
    report_path.write_bytes(b"zip")

    with pytest.raises(ValueError, match="https"):
        error_report.upload_report(report_path, "http://example.test/upload")


def test_data_parent_directory_does_not_make_log_file_sensitive(tmp_path: Path) -> None:
    base_dir = tmp_path / "data" / "arthexis"
    log_path = base_dir / "logs" / "error.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("safe diagnostic line\n", encoding="utf-8")

    assert error_report.is_sensitive_path(log_path, base_dir=base_dir) is False
    assert error_report.is_sensitive_path(base_dir / "media" / "capture.log", base_dir=base_dir) is True


def test_redact_text_removes_token_only_url_credentials() -> None:
    payload = "origin https://ghp_do-not-leak@github.com/arthexis/arthexis.git (fetch)"

    redacted = error_report.redact_text(payload)

    assert "ghp_do-not-leak" not in redacted
    assert "https://<redacted>@github.com/arthexis/arthexis.git" in redacted


def test_upload_report_uses_explicit_method(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_path = tmp_path / "report.zip"
    report_path.write_bytes(b"zip")
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 204

        def getcode(self) -> int:
            return 204

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["method"] = request.get_method()
        captured["data"] = request.data
        captured["timeout"] = timeout
        captured["content_type"] = request.headers["Content-type"]
        return FakeResponse()

    monkeypatch.setattr(error_report, "urlopen", fake_urlopen)

    status = error_report.upload_report(
        report_path,
        "https://example.test/upload",
        method="POST",
        timeout=12,
    )

    assert status == 204
    assert captured == {
        "method": "POST",
        "data": b"zip",
        "timeout": 12,
        "content_type": "application/zip",
    }


def test_flush_upstream_queue_warns_when_uploaded_file_cleanup_fails(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    queued = queue_dir / "report.zip"
    queued.write_bytes(b"zip")

    monkeypatch.setattr(error_report, "upload_report", lambda *args, **kwargs: 201)
    original_unlink = Path.unlink

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == queued:
            raise OSError("locked")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    sent = error_report._flush_upstream_queue(
        queue_dir,
        "https://example.test/upload",
        method="PUT",
        timeout=10,
        allow_insecure=False,
    )

    assert sent == [queued]
    assert queued.exists()
    assert "could not delete queued file" in capsys.readouterr().err


def test_queue_upstream_uses_unique_name_when_timestamp_collides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.zip"
    source.write_bytes(b"new")
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "report.zip").write_bytes(b"original")
    (queue_dir / "report-20260513000102.zip").write_bytes(b"same second")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 13, 0, 1, 2, tzinfo=tz)

    monkeypatch.setattr(error_report, "datetime", FixedDateTime)

    queued = error_report._queue_upstream(source, queue_dir)

    assert queued == queue_dir / "report-20260513000102-1.zip"
    assert queued.read_bytes() == b"new"
    assert (queue_dir / "report-20260513000102.zip").read_bytes() == b"same second"


def test_flush_upstream_queue_skips_non_regular_candidates(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "directory.zip").mkdir()
    queued = queue_dir / "report.zip"
    queued.write_bytes(b"zip")
    uploaded: list[Path] = []

    def fake_upload(path: Path, *args, **kwargs) -> int:
        uploaded.append(path)
        return 201

    monkeypatch.setattr(error_report, "upload_report", fake_upload)

    sent = error_report._flush_upstream_queue(
        queue_dir,
        "https://example.test/upload",
        method="PUT",
        timeout=10,
        allow_insecure=False,
    )

    assert uploaded == [queued]
    assert sent == [queued]
    assert "skipped non-regular queued file" in capsys.readouterr().err


def test_send_upstream_rejects_invalid_url_before_build(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_build(config: error_report.ReportConfig) -> error_report.ReportResult:
        raise AssertionError("build_report should not run for an invalid upstream URL")

    monkeypatch.setattr(error_report, "build_report", fail_build)

    status = error_report.main(
        [
            "--base-dir",
            str(tmp_path),
            "--send-upstream",
            "not-a-url",
        ]
    )

    assert status == 2
    assert "Invalid upstream upload URL" in capsys.readouterr().err


def test_send_upstream_relative_queue_dir_resolves_against_base_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "suite"
    base_dir.mkdir()
    report_path = base_dir / "report.zip"
    report_path.write_bytes(b"zip")
    captured: dict[str, Path] = {}

    monkeypatch.setattr(
        error_report,
        "build_report",
        lambda config: error_report.ReportResult(path=report_path, entries=[]),
    )
    monkeypatch.setattr(
        error_report,
        "upload_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )

    def fake_queue(path: Path, queue_dir: Path) -> Path:
        captured["queue_dir"] = queue_dir
        return queue_dir / path.name

    monkeypatch.setattr(error_report, "_queue_upstream", fake_queue)

    status = error_report.main(
        [
            "--base-dir",
            str(base_dir),
            "--send-upstream",
            "https://example.test/upload",
            "--upstream-queue-dir",
            "relative-queue",
        ]
    )

    assert status == 0
    assert captured["queue_dir"] == base_dir / "relative-queue"


def test_send_upstream_queue_failure_returns_controlled_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.zip"
    report_path.write_bytes(b"zip")
    monkeypatch.setattr(
        error_report,
        "build_report",
        lambda config: error_report.ReportResult(path=report_path, entries=[]),
    )
    monkeypatch.setattr(
        error_report,
        "upload_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    monkeypatch.setattr(
        error_report,
        "_queue_upstream",
        lambda path, queue_dir: (_ for _ in ()).throw(OSError("disk full")),
    )

    status = error_report.main(
        [
            "--base-dir",
            str(tmp_path),
            "--send-upstream",
            "https://example.test/upload",
        ]
    )

    assert status == 2
    assert "queueing also failed" in capsys.readouterr().err
