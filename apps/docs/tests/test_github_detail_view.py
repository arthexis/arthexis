from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.docs import views


@pytest.fixture
def staff_client(client):
    user = get_user_model().objects.create_user(
        username="docs-admin",
        password="pass",
        email="docs@example.com",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(user)
    return client


pytestmark = pytest.mark.django_db


def _mock_connection(monkeypatch):
    monkeypatch.setattr(
        views,
        "_resolve_github_docs_connection",
        lambda: SimpleNamespace(
            connected=True,
            owner="octo",
            repo="demo",
            token="tok",
            slug="octo/demo",
        ),
    )


def test_github_detail_pr_open_mergeable_renders_review_and_merge_actions(staff_client, monkeypatch):
    _mock_connection(monkeypatch)
    monkeypatch.setattr(
        views.github_service,
        "fetch_issue_or_pull_request",
        lambda **kwargs: {
            "number": 12,
            "title": "Feature PR",
            "state": "open",
            "body": "PR body",
            "pull_request": {"url": "https://api.github.test/pulls/12"},
        },
    )
    monkeypatch.setattr(views.github_service, "fetch_issue_comments", lambda **kwargs: [])
    monkeypatch.setattr(
        views.github_service,
        "fetch_pull_request",
        lambda **kwargs: {
            "state": "open",
            "mergeable": True,
            "mergeable_state": "clean",
            "requested_reviewers": [{"login": "alice"}],
            "head": {"sha": "abc123"},
        },
    )
    monkeypatch.setattr(
        views.github_service,
        "fetch_pull_request_reviews",
        lambda **kwargs: [{"state": "APPROVED"}, {"state": "CHANGES_REQUESTED"}],
    )
    monkeypatch.setattr(
        views.github_service,
        "fetch_pull_request_review_comments",
        lambda **kwargs: [{"id": 1}, {"id": 2, "in_reply_to_id": 1}],
    )
    monkeypatch.setattr(
        views.github_service,
        "fetch_commit_status_summary",
        lambda **kwargs: {"state": "success"},
    )

    response = staff_client.get(reverse("docs:docs-github-item", kwargs={"number": 12}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Review Summary" in content
    assert "Approved" in content
    assert "Changes requested" in content
    assert "Merge pull request" in content
    assert "disabled" not in content.split("Merge pull request")[0][-120:]


def test_github_detail_pr_closed_disables_merge_and_shows_guardrail(staff_client, monkeypatch):
    _mock_connection(monkeypatch)
    monkeypatch.setattr(
        views.github_service,
        "fetch_issue_or_pull_request",
        lambda **kwargs: {
            "number": 14,
            "title": "Closed PR",
            "state": "closed",
            "body": "Closed",
            "pull_request": {"url": "https://api.github.test/pulls/14"},
        },
    )
    monkeypatch.setattr(views.github_service, "fetch_issue_comments", lambda **kwargs: [])
    monkeypatch.setattr(
        views.github_service,
        "fetch_pull_request",
        lambda **kwargs: {
            "state": "closed",
            "mergeable": False,
            "mergeable_state": "dirty",
            "requested_reviewers": [],
            "head": {"sha": "abc123"},
        },
    )
    monkeypatch.setattr(views.github_service, "fetch_pull_request_reviews", lambda **kwargs: [])
    monkeypatch.setattr(views.github_service, "fetch_pull_request_review_comments", lambda **kwargs: [])
    monkeypatch.setattr(views.github_service, "fetch_commit_status_summary", lambda **kwargs: {"state": "failure"})

    response = staff_client.get(reverse("docs:docs-github-item", kwargs={"number": 14}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Pull request is closed." in content
    assert 'disabled aria-disabled="true"' in content


def test_github_detail_pr_merge_post_surfaces_merge_error(staff_client, monkeypatch):
    _mock_connection(monkeypatch)
    monkeypatch.setattr(
        views.github_service,
        "fetch_issue_or_pull_request",
        lambda **kwargs: {
            "number": 15,
            "title": "Blocked PR",
            "state": "open",
            "body": "Blocked",
            "pull_request": {"url": "https://api.github.test/pulls/15"},
        },
    )
    monkeypatch.setattr(views.github_service, "fetch_issue_comments", lambda **kwargs: [])
    monkeypatch.setattr(
        views.github_service,
        "fetch_pull_request",
        lambda **kwargs: {
            "state": "open",
            "mergeable": None,
            "mergeable_state": "unknown",
            "requested_reviewers": [],
            "head": {"sha": ""},
        },
    )
    monkeypatch.setattr(views.github_service, "fetch_pull_request_reviews", lambda **kwargs: [])
    monkeypatch.setattr(views.github_service, "fetch_pull_request_review_comments", lambda **kwargs: [])

    def fail_merge(**kwargs):
        raise views.GitHubRepositoryError("Mergeability is unknown")

    monkeypatch.setattr(views.github_service, "merge_pull_request", fail_merge)

    response = staff_client.post(
        reverse("docs:docs-github-item", kwargs={"number": 15}),
        data={"action": "pr_merge", "merge_method": "squash"},
    )

    assert response.status_code == 200
    assert "Mergeability is unknown" in response.content.decode()
