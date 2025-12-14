from __future__ import annotations

from datetime import datetime
from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.core import changelog
from apps.core.management.commands import show_changelog as show_changelog_module
from apps.core.system import _format_timestamp


@override_settings(DATETIME_FORMAT="Y-m-d H:i")
def test_show_changelog_uses_shared_timestamp_format(monkeypatch):
    commit_time = timezone.make_aware(
        datetime(2024, 1, 2, 3, 4, 5), timezone=timezone.get_current_timezone()
    )
    commit = changelog.ChangelogCommit(
        sha="1234567890abcdef",
        summary="Test commit",
        author="Jane Doe",
        authored_at=commit_time,
    )
    section = changelog.ChangelogSection(
        slug="unreleased",
        title="Unreleased",
        commits=(commit,),
        is_unreleased=True,
    )
    page = changelog.ChangelogPage((section,), None, False)

    monkeypatch.setattr(show_changelog_module.changelog, "get_initial_page", lambda initial_count=1: page)

    stdout = StringIO()
    call_command("show_changelog", "--n", "1", stdout=stdout)

    expected_timestamp = _format_timestamp(commit_time)
    output = stdout.getvalue()

    assert expected_timestamp in output
    assert f"[{commit.short_sha}] {commit.summary} — {commit.author} [{expected_timestamp}]" in output
