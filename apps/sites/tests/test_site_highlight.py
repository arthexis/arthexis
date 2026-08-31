"""Tests for public-site highlight context selection."""

from __future__ import annotations

from datetime import date, timedelta
from http.client import IncompleteRead, RemoteDisconnected

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.sites import context_processors
from apps.sites.models import SiteHighlight

pytestmark = pytest.mark.django_db

FUNDING_ISSUE_URL = "https://github.com/example/project/issues/1"


def test_load_latest_site_highlight_prefers_recent_enabled_date() -> None:
    SiteHighlight.objects.create(
        title="Old but enabled",
        highlight_date=date(2026, 3, 1),
        story="Older update",
        is_enabled=True,
    )
    SiteHighlight.objects.create(
        title="Newest disabled",
        highlight_date=date(2026, 4, 20),
        story="Should not show",
        is_enabled=False,
    )
    newest_enabled = SiteHighlight.objects.create(
        title="Newest enabled",
        highlight_date=date(2026, 4, 19),
        story="Visible update",
        is_enabled=True,
    )

    selected = context_processors._load_latest_site_highlight()

    assert selected is not None
    assert selected.pk == newest_enabled.pk


def test_load_latest_site_highlight_prefers_recent_update_for_same_date() -> None:
    older = SiteHighlight.objects.create(
        title="First version",
        highlight_date=date(2026, 4, 21),
        story="Initial copy",
        is_enabled=True,
    )
    newer = SiteHighlight.objects.create(
        title="Second version",
        highlight_date=date(2026, 4, 21),
        story="Updated copy",
        is_enabled=True,
    )
    SiteHighlight.objects.filter(pk=older.pk).update(
        updated_at=newer.updated_at + timedelta(seconds=1)
    )

    selected = context_processors._load_latest_site_highlight()

    assert selected is not None
    assert selected.pk == older.pk


def test_nav_links_includes_selected_site_highlight(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    highlight = SiteHighlight.objects.create(
        title="Navbar-adjacent highlight",
        highlight_date=date(2026, 4, 22),
        story="Story with https://example.com",
        is_enabled=True,
    )
    request = rf.get("/")
    request.user = AnonymousUser()

    monkeypatch.setattr(
        context_processors,
        "_build_chat_context",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (None, None, None),
    )
    monkeypatch.setattr(context_processors, "_load_header_references", lambda *args: [])
    monkeypatch.setattr(context_processors, "_load_visible_modules", lambda *args: [])
    monkeypatch.setattr(
        context_processors, "_parse_user_story_attachment_limit", lambda: 3
    )
    monkeypatch.setattr(
        context_processors, "_select_current_module", lambda *args: None
    )
    monkeypatch.setattr(context_processors, "_select_favicon_url", lambda *args: "")
    monkeypatch.setattr(context_processors, "_select_site_template", lambda *args: None)

    context = context_processors.nav_links(request)

    assert context["site_highlight"] is not None
    assert context["site_highlight"].pk == highlight.pk


def test_favicon_fallback_uses_tracked_fixture() -> None:
    favicon_url = context_processors._select_favicon_url(
        current_module=None,
        site=None,
        node=None,
    )

    assert favicon_url.startswith("data:image/png;base64,")
    assert len(favicon_url) > len("data:image/png;base64,")


def test_funding_banner_only_shows_on_arthexis_dot_com(
    rf: RequestFactory,
    settings,
    monkeypatch,
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com", "example.com"]
    settings.ARTHEXIS_FUNDING_ISSUE_URL = FUNDING_ISSUE_URL
    issue_checks = []

    def issue_is_open(issue_url):
        issue_checks.append(issue_url)
        return True

    monkeypatch.setattr(context_processors, "_is_github_issue_open", issue_is_open)

    canonical_request = rf.get("/", HTTP_HOST="arthexis.com")
    other_request = rf.get("/", HTTP_HOST="example.com")

    banner = context_processors._build_funding_banner(canonical_request)

    assert banner is not None
    assert banner["issue_url"] == FUNDING_ISSUE_URL
    assert issue_checks == [FUNDING_ISSUE_URL]
    assert context_processors._build_funding_banner(other_request) is None


def test_funding_banner_is_hidden_when_issue_is_closed(
    rf: RequestFactory, settings, monkeypatch
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com"]
    settings.ARTHEXIS_FUNDING_ISSUE_URL = FUNDING_ISSUE_URL
    monkeypatch.setattr(
        context_processors, "_is_github_issue_open", lambda *_args, **_kwargs: False
    )

    canonical_request = rf.get("/", HTTP_HOST="arthexis.com")

    assert context_processors._build_funding_banner(canonical_request) is None


def test_funding_banner_is_hidden_when_issue_url_setting_is_blank(
    rf: RequestFactory, settings, monkeypatch
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com"]
    settings.ARTHEXIS_FUNDING_ISSUE_URL = ""

    def fail_issue_lookup(*_args, **_kwargs):
        raise AssertionError("blank funding issue must not check issue state")

    monkeypatch.setattr(context_processors, "_is_github_issue_open", fail_issue_lookup)

    assert (
        context_processors._build_funding_banner(
            rf.get("/", HTTP_HOST="arthexis.com")
        )
        is None
    )


def test_funding_banner_is_hidden_when_issue_url_is_not_parseable(
    rf: RequestFactory, settings, monkeypatch
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com"]
    settings.ARTHEXIS_FUNDING_ISSUE_URL = 7433

    def fail_issue_lookup(*_args, **_kwargs):
        raise AssertionError("invalid funding issue must not check issue state")

    monkeypatch.setattr(context_processors, "_is_github_issue_open", fail_issue_lookup)

    assert (
        context_processors._build_funding_banner(
            rf.get("/", HTTP_HOST="arthexis.com")
        )
        is None
    )


def test_funding_banner_strips_configured_issue_url(
    rf: RequestFactory, settings, monkeypatch
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com"]
    settings.ARTHEXIS_FUNDING_ISSUE_URL = f"  {FUNDING_ISSUE_URL}  "
    issue_checks = []

    def issue_is_open(issue_url):
        issue_checks.append(issue_url)
        return True

    monkeypatch.setattr(context_processors, "_is_github_issue_open", issue_is_open)

    banner = context_processors._build_funding_banner(
        rf.get("/", HTTP_HOST="arthexis.com")
    )

    assert banner is not None
    assert banner["issue_url"] == FUNDING_ISSUE_URL
    assert issue_checks == [FUNDING_ISSUE_URL]
    assert context_processors._github_issue_api_url(True) is None


def test_github_issue_state_uses_json_response(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"state": "closed"}'

    monkeypatch.setattr(
        context_processors, "urlopen", lambda *_args, **_kwargs: Response()
    )

    assert (
        context_processors._read_github_issue_state(
            FUNDING_ISSUE_URL
        )
        == "closed"
    )


def test_github_issue_state_treats_incomplete_read_as_unknown(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            raise IncompleteRead(b'{"state":')

    monkeypatch.setattr(
        context_processors, "urlopen", lambda *_args, **_kwargs: Response()
    )

    assert (
        context_processors._read_github_issue_state(
            FUNDING_ISSUE_URL
        )
        is None
    )


def test_github_issue_state_treats_remote_disconnect_as_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        context_processors,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RemoteDisconnected("remote closed connection")
        ),
    )

    assert (
        context_processors._read_github_issue_state(
            FUNDING_ISSUE_URL
        )
        is None
    )


def test_github_issue_state_treats_socket_os_error_as_unknown(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            raise OSError("socket read failed")

    monkeypatch.setattr(
        context_processors, "urlopen", lambda *_args, **_kwargs: Response()
    )

    assert (
        context_processors._read_github_issue_state(
            FUNDING_ISSUE_URL
        )
        is None
    )


def test_github_issue_open_hides_banner_on_fetch_failure(monkeypatch) -> None:
    calls = 0

    def failing_reader(_issue_url):
        nonlocal calls
        calls += 1
        return None

    cache_key = "sites:funding_issue_state:" f"{FUNDING_ISSUE_URL}"
    context_processors.cache.delete(cache_key)
    monkeypatch.setattr(context_processors, "_read_github_issue_state", failing_reader)

    assert (
        context_processors._is_github_issue_open(
            FUNDING_ISSUE_URL
        )
        is False
    )
    assert (
        context_processors._is_github_issue_open(
            FUNDING_ISSUE_URL
        )
        is False
    )
    assert calls == 1


def test_funding_banner_skip_does_not_check_issue_state(
    rf: RequestFactory, settings, monkeypatch
) -> None:
    settings.ALLOWED_HOSTS = ["arthexis.com"]
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.hide_funding_banner = True

    def fail_issue_lookup(*_args, **_kwargs):
        raise AssertionError("hidden funding banner must not check issue state")

    monkeypatch.setattr(context_processors, "_is_github_issue_open", fail_issue_lookup)

    assert context_processors._build_funding_banner(request) is None
