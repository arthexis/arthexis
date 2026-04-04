from __future__ import annotations

from django.conf import settings

from apps.logbook.forms import LogbookEntryForm
from apps.logbook.views import LogbookCreateView


def test_logbook_form_hides_rfid_scan_log(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "app.log").write_text("ok", encoding="utf-8")
    (logs_dir / "rfid-scans.ndjson").write_text("secret", encoding="utf-8")

    monkeypatch.setattr(settings, "LOG_DIR", str(logs_dir), raising=False)

    form = LogbookEntryForm()

    assert ("app.log", "app.log") in form.fields["logs"].choices
    assert ("rfid-scans.ndjson", "rfid-scans.ndjson") not in form.fields["logs"].choices


def test_persist_logs_rejects_disallowed_log_name(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "rfid-scans.ndjson").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(settings, "LOG_DIR", str(logs_dir), raising=False)

    class _DummyEntry:
        secret = "abc123"

    def _unexpected_attachment(*args, **kwargs):
        raise AssertionError("Disallowed log should not be attached")

    monkeypatch.setattr("apps.logbook.views.LogbookLogAttachment", _unexpected_attachment)

    view = LogbookCreateView()
    view._persist_logs(_DummyEntry(), ["rfid-scans.ndjson"])
