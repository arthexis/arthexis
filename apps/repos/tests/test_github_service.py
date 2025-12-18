from __future__ import annotations

from typing import Any

import pytest

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
