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
def test_repo_command_lists_issues_for_positional_repo(monkeypatch):
    """repo issues list should accept a positional repository slug."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_issues",
        lambda self, repository, state="open": [
            {"number": 7, "state": state, "title": f"{repository.slug}-{state}"}
        ],
    )

    out = StringIO()
    call_command("repo", "issues", "list", "octo/demo", "--state", "closed", stdout=out)

    output = out.getvalue()
    assert "#7 [closed] octo/demo-closed" in output
    assert "Listed 1 issues from octo/demo" in output


@pytest.mark.django_db
def test_repo_command_lists_prs_for_positional_repo(monkeypatch):
    """repo prs list should accept a positional repository slug."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_pull_requests",
        lambda self, repository, state="open": [
            {"number": 3, "state": state, "title": f"{repository.slug}-{state}"}
        ],
    )

    out = StringIO()
    call_command("repo", "prs", "list", "octo/demo", stdout=out)

    output = out.getvalue()
    assert "#3 [open] octo/demo-open" in output
    assert "Listed 1 pull requests from octo/demo" in output


@pytest.mark.django_db
def test_repo_command_creates_release_for_positional_repo(monkeypatch):
    """repo releases create should accept a positional repository slug."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "create_release",
        lambda self, repository, tag, title, notes="": f"{repository.slug}:{tag}:{title}:{notes}",
    )

    out = StringIO()
    call_command(
        "repo",
        "releases",
        "create",
        "octo/demo",
        "--tag",
        "v1.2.3",
        "--title",
        "Release v1.2.3",
        "--notes",
        "Short notes",
        stdout=out,
    )

    assert "octo/demo:v1.2.3:Release v1.2.3:Short notes" in out.getvalue()


@pytest.mark.django_db
def test_repo_command_accepts_matching_repo_option_and_positional_repo(monkeypatch):
    """repo command should allow matching repository inputs in both forms."""

    monkeypatch.setattr(
        ReleaseManagementClient,
        "list_issues",
        lambda self, repository, state="open": [
            {"number": 2, "state": state, "title": repository.slug}
        ],
    )

    out = StringIO()
    call_command("repo", "--repo", "octo/demo", "issues", "list", "octo/demo", stdout=out)

    output = out.getvalue()
    assert "#2 [open] octo/demo" in output
    assert "Listed 1 issues from octo/demo" in output


@pytest.mark.django_db
def test_repo_command_rejects_conflicting_repo_option_and_positional_repo():
    """repo command should reject mismatched repository inputs across both forms."""

    with pytest.raises(CommandError, match="must match"):
        call_command("repo", "--repo", "octo/demo", "issues", "list", "other/demo")


@pytest.mark.django_db
def test_repo_command_requires_owner_name_format_for_repo_option():
    """repo command should reject malformed --repo values."""

    with pytest.raises(CommandError, match="owner/name"):
        call_command("repo", "--repo", "bad-format", "issues", "list")


@pytest.mark.django_db
def test_repo_command_requires_owner_name_format_for_positional_repo():
    """repo command should reject malformed positional repository values."""

    with pytest.raises(CommandError, match="owner/name"):
        call_command("repo", "issues", "list", "bad-format")


@pytest.mark.django_db
def test_repo_command_wraps_malformed_repo_url_as_command_error():
    """URL parse errors should surface as command validation failures."""

    with pytest.raises(CommandError):
        call_command("repo", "--repo", "https://github.com/owner", "issues", "list")


@pytest.mark.django_db
def test_repo_command_rejects_extra_path_segments_in_repo_option():
    """repo command should reject owner/name values with extra segments."""

    with pytest.raises(CommandError, match="owner/name"):
        call_command("repo", "--repo", "owner/name/extra", "issues", "list")
