"""Tests for the repo management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.repos.release_management import ReleaseManagementClient


@pytest.mark.django_db
def test_repo_command_lists_issues_for_explicit_repo(monkeypatch):
    """repo issues list should render issue rows for an explicit repository."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_issues",
        lambda self, repository, state="open": [
            {"number": 1, "state": "open", "title": f"{repository.slug}-{state}"}
        ],
    )

    out = StringIO()
    call_command("repo", "--repo", "octo/demo", "issues", "list", stdout=out)

    output = out.getvalue()
    assert "#1 [open] octo/demo-open" in output
    assert "Listed 1 issues from octo/demo" in output


@pytest.mark.django_db
def test_repo_command_requires_owner_name_format_for_repo_option():
    """repo command should reject malformed --repo values."""

    with pytest.raises(CommandError, match="owner/name"):
        call_command("repo", "--repo", "bad-format", "issues", "list")


@pytest.mark.django_db
def test_repo_command_wraps_malformed_repo_url_as_command_error():
    """URL parse errors should surface as command validation failures."""

    with pytest.raises(CommandError):
        call_command("repo", "--repo", "https://github.com/owner", "issues", "list")
