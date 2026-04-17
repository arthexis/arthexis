from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from apps.repos.services import github


class DummyResponse:
    def __init__(self, data: Any, status_code: int = 200, links: dict | None = None, text: str = ""):
        self._data = data
        self.status_code = status_code
        self.links = links or {}
        self.text = text or ""
        self.closed = False

    def json(self):
        return self._data

    def close(self):
        self.closed = True


def test_issue_lock_dir_uses_project_root():
    assert github.ISSUE_LOCK_DIR == Path(settings.BASE_DIR) / ".locks" / "github-issues"


def test_fetch_repository_issues_handles_pagination(monkeypatch):
    calls: list[dict[str, Any]] = []
    responses = [
        DummyResponse(
            [{"number": 1}, {"number": 2}],
            links={"next": {"url": "https://api.github.com/repos/octo/demo/issues?page=2"}},
        ),
        DummyResponse([{"number": 3}]),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(github.requests, "get", fake_get)

    items = list(github.fetch_repository_issues(token="tok", owner="octo", name="demo"))

    assert [item["number"] for item in items] == [1, 2, 3]
    assert calls[0]["params"] == {"state": "open", "per_page": 100}
    assert calls[1]["params"] is None  # pagination should clear params


def test_fetch_repository_pull_requests_raises_on_error(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        return DummyResponse({"message": "Nope"}, status_code=500, links={}, text="boom")

    monkeypatch.setattr(github.requests, "get", fake_get)

    with pytest.raises(github.GitHubRepositoryError):
        list(github.fetch_repository_pull_requests(token="tok", owner="octo", name="demo"))


def test_resolve_repository_token_uses_latest_release_when_available(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr(github, "_get_latest_release_token", lambda: "release-token")

    token = github.resolve_repository_token(package=None)

    assert token == "release-token"


def test_create_pull_request_comment_posts_to_issue_comments_for_open_pr(monkeypatch):
    calls: dict[str, dict[str, Any]] = {}

    def fake_get(url, headers=None, timeout=None):
        calls["get"] = {"url": url, "headers": headers, "timeout": timeout}
        return DummyResponse({"state": "open"})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"id": 1}, status_code=201)

    monkeypatch.setattr(github.requests, "get", fake_get)
    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.create_pull_request_comment(
        "octo",
        "demo",
        pull_number=12,
        token="tok",
        body="Looks good",
    )

    assert response.status_code == 201
    assert calls["get"]["url"].endswith("/repos/octo/demo/pulls/12")
    assert calls["post"]["url"].endswith("/repos/octo/demo/issues/12/comments")
    assert calls["post"]["json"] == {"body": "Looks good"}


def test_create_pull_request_comment_rejects_closed_pr(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({"state": "closed"})

    monkeypatch.setattr(github.requests, "get", fake_get)

    with pytest.raises(github.GitHubRepositoryError, match="not open"):
        github.create_pull_request_comment(
            "octo",
            "demo",
            pull_number=12,
            token="tok",
            body="Please merge",
        )


def test_create_issue_comment_posts_to_issue_comments(monkeypatch):
    calls: dict[str, dict[str, Any]] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"id": 2}, status_code=201)

    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.create_issue_comment(
        "octo",
        "demo",
        issue_number=34,
        token="tok",
        body="Please attach logs.",
    )

    assert response.status_code == 201
    assert calls["post"]["url"].endswith("/repos/octo/demo/issues/34/comments")
    assert calls["post"]["json"] == {"body": "Please attach logs."}


def test_close_issue_patches_closed_state(monkeypatch):
    calls: dict[str, dict[str, Any]] = {}

    def fake_patch(url, json=None, headers=None, timeout=None):
        calls["patch"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"state": "closed"}, status_code=200)

    monkeypatch.setattr(github.requests, "patch", fake_patch)

    response = github.close_issue(
        "octo",
        "demo",
        issue_number=35,
        token="tok",
    )

    assert response.status_code == 200
    assert calls["patch"]["url"].endswith("/repos/octo/demo/issues/35")
    assert calls["patch"]["json"] == {"state": "closed"}


def test_mark_pull_request_ready_uses_graphql_mutation(monkeypatch):
    calls: dict[str, dict[str, Any]] = {}

    def fake_get(url, headers=None, timeout=None):
        calls["get"] = {"url": url, "headers": headers, "timeout": timeout}
        return DummyResponse({"node_id": "PR_node_123", "state": "open"})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse(
            {
                "data": {
                    "markPullRequestReadyForReview": {
                        "pullRequest": {"number": 36, "isDraft": False}
                    }
                }
            },
            status_code=200,
        )

    monkeypatch.setattr(github.requests, "get", fake_get)
    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.mark_pull_request_ready(
        "octo",
        "demo",
        pull_number=36,
        token="tok",
    )

    assert response.status_code == 200
    assert calls["get"]["url"].endswith("/repos/octo/demo/pulls/36")
    assert calls["post"]["url"] == github.GRAPHQL_ROOT
    assert calls["post"]["json"]["variables"] == {"pullRequestId": "PR_node_123"}


def test_merge_pull_request_puts_selected_method(monkeypatch):
    calls: dict[str, dict[str, Any]] = {}

    def fake_put(url, json=None, headers=None, timeout=None):
        calls["put"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"merged": True}, status_code=200)

    monkeypatch.setattr(github.requests, "put", fake_put)

    response = github.merge_pull_request(
        "octo",
        "demo",
        pull_number=37,
        token="tok",
        merge_method="squash",
    )

    assert response.status_code == 200
    assert calls["put"]["url"].endswith("/repos/octo/demo/pulls/37/merge")
    assert calls["put"]["json"] == {"merge_method": "squash"}
