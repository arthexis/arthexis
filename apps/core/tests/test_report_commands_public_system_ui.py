from __future__ import annotations

import io
from datetime import datetime, timezone

from django.core.management import call_command

from apps.core import changelog


def test_report_startup_command_uses_public_helper(monkeypatch):
    """report_startup renders entries from the new public helper import path."""

    monkeypatch.setattr(
        "apps.core.management.commands.startup.read_startup_report",
        lambda **_kwargs: {
            "entries": [
                {
                    "timestamp_label": "2024-01-01 10:00",
                    "script": "start.sh",
                    "event": "ok",
                    "detail": "booted",
                }
            ],
            "log_path": "/tmp/startup.log",
            "clock_warning": None,
            "error": None,
        },
    )

    stream = io.StringIO()
    call_command("startup", stdout=stream)
    output = stream.getvalue()

    assert "Startup report log: /tmp/startup.log" in output
    assert "2024-01-01 10:00 [start.sh] ok — booted" in output


def test_show_changelog_command_uses_public_timestamp_formatter(monkeypatch):
    """show_changelog uses the public timestamp formatter import."""

    commit = changelog.ChangelogCommit(
        sha="abcdef123456",
        summary="Summary",
        author="Author",
        authored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        commit_url="https://example.com/c",
    )
    section = changelog.ChangelogSection(slug="unreleased", title="Unreleased", commits=(commit,), is_unreleased=True)
    page = changelog.ChangelogPage(sections=(section,), next_page=None, has_more=False)

    monkeypatch.setattr("apps.core.changelog.get_initial_page", lambda initial_count=1: page)
    monkeypatch.setattr("apps.core.management.commands.changelog.format_timestamp", lambda value: "TS")

    stream = io.StringIO()
    call_command("changelog", stdout=stream)
    assert "[TS]" in stream.getvalue()
