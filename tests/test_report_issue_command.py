from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone as dt_timezone

from django.core.management import call_command
from django.utils import timezone
from unittest.mock import patch


def test_report_issue_command_queues_payload(tmp_path, monkeypatch):
    log_file = tmp_path / "start.log"
    log_file.write_text("\n".join(f"line {idx}" for idx in range(120)), encoding="utf-8")

    frozen = datetime(2024, 1, 1, 12, 0, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: frozen)

    with patch("core.management.commands.report_issue.report_github_issue.delay") as delay:
        call_command(
            "report_issue",
            "--source",
            "start",
            "--command",
            "python manage.py runserver --noreload",
            "--exit-code",
            "1",
            "--host",
            "test-host",
            "--app-version",
            "9.9.9",
            "--revision",
            "abc123",
            "--log-file",
            str(log_file),
        )

    assert delay.called
    payload = delay.call_args.args[0]
    assert payload["source"] == "start"
    assert payload["command"] == "python manage.py runserver --noreload"
    assert payload["exit_code"] == 1
    assert payload["host"] == "test-host"
    assert payload["version"] == "9.9.9"
    assert payload["revision"] == "abc123"
    assert payload["captured_at"] == frozen.isoformat()

    excerpt_lines = payload["log_excerpt"].splitlines()
    assert len(excerpt_lines) == 100
    assert excerpt_lines[0] == "line 20"
    assert excerpt_lines[-1] == "line 119"

    expected_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "command": "python manage.py runserver --noreload",
                "exit_code": 1,
                "revision": "abc123",
                "source": "start",
                "version": "9.9.9",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert payload["fingerprint"] == expected_fingerprint
    assert len(payload["fingerprint"]) == 64


def test_report_issue_command_handles_missing_log():
    with patch("core.management.commands.report_issue.report_github_issue.delay") as delay:
        call_command(
            "report_issue",
            "--source",
            "start",
            "--command",
            "python manage.py collectstatic --noinput",
            "--exit-code",
            "2",
            "--host",
            "test-host",
            "--app-version",
            "9.9.9",
            "--revision",
            "abc123",
        )

    payload = delay.call_args.args[0]
    assert payload["log_excerpt"] == ""
    assert payload["exit_code"] == 2
