import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command

from core.changelog import ChangelogCommit, ChangelogPage, ChangelogSection


def _build_commit(index: int) -> ChangelogCommit:
    return ChangelogCommit(
        sha=f"abcde{index:02d}",
        summary=f"Change {index}",
        author="Tester",
        authored_at=datetime(2024, 1, index, tzinfo=timezone.utc),
        commit_url=f"https://example.com/commit/abcde{index:02d}",
    )


def test_changelog_command_outputs_recent_entries():
    commits = tuple(_build_commit(i) for i in range(1, 4))
    page = ChangelogPage(
        sections=(
            ChangelogSection(
                slug="unreleased",
                title="Unreleased",
                commits=commits,
                is_unreleased=True,
            ),
        ),
        next_page=None,
        has_more=False,
    )

    buffer = io.StringIO()
    with patch("core.management.commands.changelog.changelog.get_initial_page", return_value=page):
        call_command("changelog", "-n", "2", stdout=buffer)

    output = buffer.getvalue()
    assert "Latest changelog section: Unreleased (unreleased)" in output
    assert "Showing 2 of 3 entries" in output
    assert "[abcde01] Change 1" in output
    assert "[abcde02] Change 2" in output
    assert "[abcde03]" not in output


def test_changelog_command_handles_missing_sections():
    page = ChangelogPage(sections=tuple(), next_page=None, has_more=False)
    buffer = io.StringIO()

    with patch("core.management.commands.changelog.changelog.get_initial_page", return_value=page):
        call_command("changelog", stdout=buffer)

    assert "No changelog information is available." in buffer.getvalue()
