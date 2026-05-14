from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
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
