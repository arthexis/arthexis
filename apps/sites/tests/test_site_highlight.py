"""Tests for public-site highlight context selection."""

from __future__ import annotations

from datetime import date
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.sites import context_processors
from apps.sites.models import SiteHighlight

pytestmark = pytest.mark.django_db


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
    monkeypatch.setattr(context_processors, "_parse_user_story_attachment_limit", lambda: 3)
    monkeypatch.setattr(context_processors, "_select_current_module", lambda *args: None)
    monkeypatch.setattr(context_processors, "_select_favicon_url", lambda *args: "")
    monkeypatch.setattr(context_processors, "_select_site_template", lambda *args: None)

    context = context_processors.nav_links(request)

    assert context["site_highlight"] is not None
    assert context["site_highlight"].pk == highlight.pk
