from __future__ import annotations

import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from apps.cards.login_poll import (
    RFID_LOGIN_POLL_QUERY_PARAM,
    RFID_LOGIN_POLL_SESSION_KEY,
)

pytestmark = [pytest.mark.django_db]


def test_rfid_login_page_embeds_session_scoped_scan_url(client, monkeypatch):
    node = SimpleNamespace(
        role=None,
        has_feature=lambda slug: slug in {"rfid", "rfid-scanner"},
    )
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: node)
    monkeypatch.setattr(
        "apps.sites.views.management.ensure_feature_enabled",
        lambda *args, **kwargs: None,
    )

    response = client.get(reverse("pages:rfid-login"))

    assert response.status_code == 200
    token = client.session[RFID_LOGIN_POLL_SESSION_KEY]
    html = response.content.decode("utf-8")
    assert reverse("rfid-scan-next") in html
    parsed = urlparse(response.context["scan_api_url"])
    assert parse_qs(parsed.query)[RFID_LOGIN_POLL_QUERY_PARAM] == [token]


def _landing(path, label="Repository Work"):
    return SimpleNamespace(
        agent_notes=[],
        description="",
        label=label,
        nav_is_invalid=False,
        nav_is_locked=False,
        path=path,
    )


def _nav_module(landings):
    return SimpleNamespace(
        agent_notes=[],
        enabled_landings=landings,
        enabled_landings_all_invalid=False,
        menu_label="Repository Work",
    )


def _base_template_context(*, nav_modules):
    return {
        "feedback_ingestion_enabled": False,
        "hide_default_footer": True,
        "nav_modules": nav_modules,
    }


def test_base_template_renders_single_landing_module_direct_url():
    module = _nav_module([_landing("/repos/work/")])

    html = render_to_string(
        "pages/base.html",
        _base_template_context(nav_modules=[module]),
    )

    assert 'href="/repos/work/"' in html


def test_base_template_renders_dropdown_landing_urls_directly():
    module = _nav_module(
        [
            _landing("/repos/work/?view=open#assigned"),
            _landing("/docs/", label="Docs"),
        ]
    )

    html = render_to_string(
        "pages/base.html",
        _base_template_context(nav_modules=[module]),
    )

    assert 'href="/repos/work/?view=open#assigned"' in html
    assert 'href="/docs/"' in html


def test_base_template_does_not_rewrite_invalid_dropdown_landing_urls():
    invalid_landing = _landing("//[::1", label="Malformed")
    invalid_landing.nav_is_invalid = True
    module = _nav_module([invalid_landing, _landing("/docs/", label="Docs")])

    html = render_to_string(
        "pages/base.html",
        _base_template_context(nav_modules=[module]),
    )

    malformed_anchor = re.search(
        r"<a(?P<attrs>[^>]*)>\s*<span[^>]*>\s*<span[^>]*>\s*Malformed\s*</span>",
        html,
    )
    assert malformed_anchor is not None
    assert "//[::1" not in html
    assert 'href="#"' in malformed_anchor["attrs"]
    assert 'aria-disabled="true"' in malformed_anchor["attrs"]
    assert 'href="/docs/"' in html
