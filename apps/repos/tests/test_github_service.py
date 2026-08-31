from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from apps.repos.models import GitHubToken
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


def test_build_issue_payload_adds_critical_priority_for_ocpp_signal():
    payload = github.build_issue_payload(
        "Charge point heartbeat drift",
        "Structured payload: {'app': 'apps.ocpp', 'path': 'apps/ocpp/handlers.py'}",
        labels=("bug", "bug", "priority: high"),
    )

    assert payload == {
        "title": "Charge point heartbeat drift",
        "body": "Structured payload: {'app': 'apps.ocpp', 'path': 'apps/ocpp/handlers.py'}",
        "labels": ["bug", "priority: critical"],
    }


def test_build_issue_payload_keeps_last_priority_label():
    payload = github.build_issue_payload(
        "Conflicting priority labels",
        "The caller supplied more than one priority label.",
        labels=("bug", "priority: low", "enhancement", "priority: high"),
    )

    assert payload == {
        "title": "Conflicting priority labels",
        "body": "The caller supplied more than one priority label.",
        "labels": ["bug", "enhancement", "priority: high"],
    }


def test_build_issue_payload_adds_critical_priority_for_charger_signal():
    payload = github.build_issue_payload(
        "Charger heartbeat drift",
        "Connector telemetry is stale.",
        labels=("automation",),
    )

    assert payload == {
        "title": "Charger heartbeat drift",
        "body": "Connector telemetry is stale.",
        "labels": ["automation", "priority: critical"],
    }


def test_build_issue_payload_adds_critical_priority_for_imager_signal():
    payload = github.build_issue_payload(
        "GWAY imager burn flow failed",
        "The base image could not be prepared for USB media.",
        labels=("bug",),
    )

    assert payload == {
        "title": "GWAY imager burn flow failed",
        "body": "The base image could not be prepared for USB media.",
        "labels": ["bug", "priority: critical"],
    }


def test_build_issue_payload_preserves_non_ocpp_issue_labels():
    payload = github.build_issue_payload(
        "Dashboard screenshot failed",
        "Structured payload: {'app': 'apps.cards', 'path': 'apps/cards/views.py'}",
        labels=("bug", "bug", "priority: high"),
    )

    assert payload == {
        "title": "Dashboard screenshot failed",
        "body": "Structured payload: {'app': 'apps.cards', 'path': 'apps/cards/views.py'}",
        "labels": ["bug", "priority: high"],
    }


def test_build_issue_payload_ignores_unspecified_ocpp_template_field():
    payload = github.build_issue_payload(
        "Dashboard screenshot failed",
        (
            "### OCPP protocol version\n"
            "Not sure / Not applicable\n"
            "### What happened?\n"
            "The dashboard screenshot workflow failed."
        ),
        labels=("bug",),
    )

    assert payload == {
        "title": "Dashboard screenshot failed",
        "body": (
            "### OCPP protocol version\n"
            "Not sure / Not applicable\n"
            "### What happened?\n"
            "The dashboard screenshot workflow failed."
        ),
        "labels": ["bug"],
    }


def test_build_issue_payload_handles_missing_title_and_body():
    payload = github.build_issue_payload(None, None, labels=("bug",))

    assert payload == {"title": "", "body": "", "labels": ["bug"]}


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


def test_fetch_repository_labels_handles_pagination(monkeypatch):
    calls: list[dict[str, Any]] = []
    responses = [
        DummyResponse(
            [{"name": "bug"}],
            links={"next": {"url": "https://api.github.com/repos/octo/demo/labels?page=2"}},
        ),
        DummyResponse([{"name": "triage"}]),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(github.requests, "get", fake_get)

    labels = list(github.fetch_repository_labels(token="tok", owner="octo", name="demo"))

    assert [label["name"] for label in labels] == ["bug", "triage"]
    assert calls[0]["url"].endswith("/repos/octo/demo/labels")
    assert calls[0]["params"] == {"per_page": 100}
    assert calls[1]["url"] == "https://api.github.com/repos/octo/demo/labels?page=2"
    assert calls[1]["params"] is None


def test_fetch_repository_pull_requests_raises_on_error(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        return DummyResponse({"message": "Nope"}, status_code=500, links={}, text="boom")

    monkeypatch.setattr(github.requests, "get", fake_get)

    with pytest.raises(github.GitHubRepositoryError):
        list(github.fetch_repository_pull_requests(token="tok", owner="octo", name="demo"))


def test_resolve_repository_token_prefers_user_token_then_env(monkeypatch):
    user = type("User", (), {"is_authenticated": True})()
    env_called = False

    def fake_user_lookup(user=None):
        assert user is user_instance
        return "user-token"

    def fake_env_lookup():
        nonlocal env_called
        env_called = True
        return "env-token"

    user_instance = user
    monkeypatch.setattr(github, "_get_user_stored_token", fake_user_lookup)
    monkeypatch.setattr(github, "_get_env_token", fake_env_lookup)

    token = github.resolve_repository_token(package=None, user=user)

    assert token == "user-token"
    assert env_called is False


@pytest.mark.django_db
def test_resolve_repository_token_ignores_unresolved_user_sigil(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "env-token")
    user = get_user_model().objects.create_user(username="sigil-user")
    GitHubToken.objects.create(
        user=user,
        label="stale sigil",
        token="[ENV.GITHUB_TOKEN]",
    )

    token = github.resolve_repository_token(package=None, user=user)

    assert token == "env-token"


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


def test_create_issue_posts_ocpp_payload_with_critical_priority(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["request"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"number": 45}, status_code=201)

    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.create_issue(
        owner="octo",
        repository="demo",
        token="tok",
        title="OCPP connector fault",
        body="module=apps.ocpp.sessions path=apps/ocpp/session.py",
        labels=("automation",),
    )

    assert response.status_code == 201
    assert calls["request"]["url"].endswith("/repos/octo/demo/issues")
    assert calls["request"]["json"] == {
        "title": "OCPP connector fault",
        "body": "module=apps.ocpp.sessions path=apps/ocpp/session.py",
        "labels": ["automation", "priority: critical"],
    }


def test_create_issue_retries_without_auto_ocpp_label_when_label_missing(monkeypatch):
    calls: list[dict[str, Any]] = []
    responses = [
        DummyResponse(
            {
                "message": "Validation Failed",
                "errors": [{"field": "labels", "message": "Label does not exist"}],
            },
            status_code=422,
            text="label missing",
        ),
        DummyResponse({"number": 46}, status_code=201),
    ]

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return responses.pop(0)

    monkeypatch.setattr(github.requests, "post", fake_post)

    response = github.create_issue(
        owner="octo",
        repository="demo",
        token="tok",
        title="OCPP connector fault",
        body="module=apps.ocpp.sessions path=apps/ocpp/session.py",
        labels=("automation",),
    )

    assert response.status_code == 201
    assert calls[0]["json"] == {
        "title": "OCPP connector fault",
        "body": "module=apps.ocpp.sessions path=apps/ocpp/session.py",
        "labels": ["automation", "priority: critical"],
    }
    assert calls[1]["json"] == {
        "title": "OCPP connector fault",
        "body": "module=apps.ocpp.sessions path=apps/ocpp/session.py",
        "labels": ["automation"],
    }


def test_remove_issue_label_deletes_encoded_label(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_delete(url, headers=None, timeout=None):
        calls["request"] = {
            "url": url,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse([], status_code=200)

    monkeypatch.setattr(github.requests, "delete", fake_delete)

    response = github.remove_issue_label(
        owner="octo",
        repository="demo",
        issue_number=33,
        token="tok",
        label="needs review",
    )

    assert response.status_code == 200
    assert calls["request"]["url"].endswith(
        "/repos/octo/demo/issues/33/labels/needs%20review"
    )


def test_remove_issue_label_can_ignore_missing_label(monkeypatch):
    def fake_delete(url, headers=None, timeout=None):
        return DummyResponse({"message": "Not Found"}, status_code=404)

    monkeypatch.setattr(github.requests, "delete", fake_delete)

    response = github.remove_issue_label(
        owner="octo",
        repository="demo",
        issue_number=33,
        token="tok",
        label="priority: low",
        ignore_missing=True,
    )

    assert response.status_code == 404


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


def test_reopen_issue_patches_open_state(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_patch(url, json=None, headers=None, timeout=None):
        calls["request"] = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return DummyResponse({"state": "open"}, status_code=200)

    monkeypatch.setattr(github.requests, "patch", fake_patch)

    response = github.reopen_issue(
        owner="octo",
        repository="demo",
        issue_number=44,
        token="tok",
    )

    assert response.status_code == 200
    assert calls["request"]["url"].endswith("/repos/octo/demo/issues/44")
    assert calls["request"]["json"] == {"state": "open"}


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
