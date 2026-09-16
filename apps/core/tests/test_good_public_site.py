from __future__ import annotations

import subprocess
from urllib.error import URLError

import pytest

from apps.core.good import (
    _check_public_site_reachability,
    _resolve_public_site_url,
)


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def getcode(self) -> int:
        return self.status_code


def _configure_public_site(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr("apps.core.good.shutil.which", lambda name: "/usr/bin/gway")

    def _run(command, **kwargs):
        assert command == [
            "/usr/bin/gway",
            "web",
            "site",
            "arthexis",
            "--url",
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, stdout=f"{url}\n", stderr="")

    monkeypatch.setattr("apps.core.good.subprocess.run", _run)


def test_resolve_public_site_url_routes_through_gway_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_public_site(monkeypatch, "https://example.com")

    assert _resolve_public_site_url() == "https://example.com"


def test_public_site_reachability_accepts_successful_public_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_public_site(monkeypatch, "https://example.com")
    monkeypatch.setattr(
        "apps.core.good.urlopen",
        lambda url, **kwargs: _Response(200),
    )

    assert list(_check_public_site_reachability()) == []


def test_public_site_reachability_reports_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_public_site(monkeypatch, "https://example.com")

    def _fail(url, **kwargs):
        del url, kwargs
        raise URLError("DNS lookup failed")

    monkeypatch.setattr("apps.core.good.urlopen", _fail)

    issues = list(_check_public_site_reachability())

    assert len(issues) == 1
    issue = issues[0]
    assert issue.key == "public-site-unreachable"
    assert issue.severity == "important"
    assert issue.category == "availability"
    assert issue.detail.startswith("GWAY Web advertises https://example.com,")
    assert "DNS lookup failed" in issue.detail


def test_public_site_reachability_reports_http_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_public_site(monkeypatch, "https://example.com")
    monkeypatch.setattr(
        "apps.core.good.urlopen",
        lambda url, **kwargs: _Response(503),
    )

    issues = list(_check_public_site_reachability())

    assert len(issues) == 1
    assert issues[0].key == "public-site-unreachable"
    assert "status 503" in issues[0].detail


def test_public_site_check_is_optional_when_gway_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.core.good.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "apps.core.good.urlopen",
        lambda *args, **kwargs: pytest.fail("public URL should not be probed"),
    )

    assert list(_check_public_site_reachability()) == []


def test_public_site_check_is_optional_when_site_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.core.good.shutil.which", lambda name: "/usr/bin/gway")
    monkeypatch.setattr(
        "apps.core.good.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="unknown site: arthexis",
        ),
    )
    monkeypatch.setattr(
        "apps.core.good.urlopen",
        lambda *args, **kwargs: pytest.fail("public URL should not be probed"),
    )

    assert list(_check_public_site_reachability()) == []
