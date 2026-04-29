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


def test_resolve_repository_token_prefers_user_token_then_env(monkeypatch):
    user = type("User", (), {"is_authenticated": True})()
    monkeypatch.setattr(github, "_get_user_stored_token", lambda user=None: "user-token")
    monkeypatch.setattr(github, "_get_env_token", lambda: "env-token")

    token = github.resolve_repository_token(package=None, user=user)

    assert token == "user-token"


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


def test_add_issue_labels_posts_label_payload(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["request"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"labels": ["spam-suspected"]}, status_code=200)

    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.add_issue_labels(
        owner="octo",
        repository="demo",
        issue_number=33,
        token="tok",
        labels=("spam-suspected", "triage"),
    )

    assert response.status_code == 200
    assert calls["request"]["url"].endswith("/repos/octo/demo/issues/33/labels")
    assert calls["request"]["json"] == {"labels": ["spam-suspected", "triage"]}


def test_close_issue_patches_closed_state(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_patch(url, json=None, headers=None, timeout=None):
        calls["request"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"state": "closed"}, status_code=200)

    monkeypatch.setattr(github.requests, "patch", fake_patch)

    response = github.close_issue(
        owner="octo",
        repository="demo",
        issue_number=44,
        token="tok",
    )

    assert response.status_code == 200
    assert calls["request"]["url"].endswith("/repos/octo/demo/issues/44")
    assert calls["request"]["json"] == {"state": "closed"}


def test_submit_pull_request_review_decision_posts_review_event(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["request"] = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return DummyResponse({"id": 99}, status_code=200)

    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.submit_pull_request_review_decision(
        owner="octo",
        repository="demo",
        pull_number=7,
        token="tok",
        decision="APPROVE",
        body="Ship it",
    )

    assert response.status_code == 200
    assert calls["request"]["url"].endswith("/repos/octo/demo/pulls/7/reviews")
    assert calls["request"]["json"] == {"event": "APPROVE", "body": "Ship it"}


def test_submit_pull_request_review_decision_surfaces_api_validation_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return DummyResponse(
            {"message": "Review cannot be submitted on a closed pull request"},
            status_code=422,
            text="unprocessable",
        )

    monkeypatch.setattr(github.requests, "post", fake_post)

    with pytest.raises(github.GitHubRepositoryError, match="closed pull request"):
        github.submit_pull_request_review_decision(
            owner="octo",
            repository="demo",
            pull_number=7,
            token="tok",
            decision="COMMENT",
            body="Needs follow-up",
        )


def test_merge_pull_request_rejects_unknown_mergeability(monkeypatch):
    monkeypatch.setattr(
        github,
        "fetch_pull_request",
        lambda **kwargs: {"state": "open", "mergeable": None, "mergeable_state": "unknown"},
    )

    with pytest.raises(github.GitHubRepositoryError, match="being calculated"):
        github.merge_pull_request(
            owner="octo",
            repository="demo",
            pull_number=22,
            token="tok",
        )


def test_merge_pull_request_calls_merge_endpoint(monkeypatch):
    calls: dict[str, Any] = {}

    monkeypatch.setattr(
        github,
        "fetch_pull_request",
        lambda **kwargs: {
            "state": "open",
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": "head123"},
        },
    )

    def fake_put(url, json=None, headers=None, timeout=None):
        calls["request"] = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return DummyResponse({"merged": True, "message": "Pull Request successfully merged"}, status_code=200)

    monkeypatch.setattr(github.requests, "put", fake_put)

    payload = github.merge_pull_request(
        owner="octo",
        repository="demo",
        pull_number=22,
        token="tok",
        merge_method="squash",
        commit_title="Merge feature",
        commit_message="Includes tests",
    )

    assert payload["merged"] is True
    assert calls["request"]["url"].endswith("/repos/octo/demo/pulls/22/merge")
    assert calls["request"]["json"] == {
        "merge_method": "squash",
        "sha": "head123",
        "commit_title": "Merge feature",
        "commit_message": "Includes tests",
    }


def test_merge_pull_request_rejects_when_expected_head_sha_is_stale(monkeypatch):
    monkeypatch.setattr(
        github,
        "fetch_pull_request",
        lambda **kwargs: {
            "state": "open",
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": "head123"},
        },
    )

    with pytest.raises(github.GitHubRepositoryError, match="head changed"):
        github.merge_pull_request(
            owner="octo",
            repository="demo",
            pull_number=22,
            token="tok",
            expected_head_sha="different",
        )
