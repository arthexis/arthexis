from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from apps.cards import views


pytestmark = pytest.mark.django_db


def _make_node(role_name: str) -> SimpleNamespace:
    return SimpleNamespace(role=SimpleNamespace(name=role_name))


def test_scan_next_anonymous_html_get_redirects_for_non_control_role(monkeypatch):
    """scan_next uses Node.get_local; role.name == "Control" allows anonymous."""
    node = _make_node("Operator")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)

    factory = RequestFactory()
    request = factory.get(reverse("rfid-scan-next"))
    request.user = AnonymousUser()

    response = views.scan_next(request)

    assert response.status_code == 302


def test_scan_next_anonymous_json_requests_unauthorized_for_non_control_role(monkeypatch):
    node = _make_node("Reader")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)

    factory = RequestFactory()
    get_request = factory.get(
        reverse("rfid-scan-next"),
        HTTP_ACCEPT="application/json",
    )
    get_request.user = AnonymousUser()

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 401
    assert json.loads(get_response.content) == {"error": "Authentication required"}

    post_request = factory.post(
        reverse("rfid-scan-next"),
        data=json.dumps({"rfid": "deadbeef"}),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )
    post_request.user = AnonymousUser()

    post_response = views.scan_next(post_request)

    assert post_response.status_code == 401
    assert json.loads(post_response.content) == {"error": "Authentication required"}


def test_scan_next_allows_anonymous_for_control_role(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)
    monkeypatch.setattr(views, "scan_sources", lambda *_args, **_kwargs: {"rfid": "scan_next"})
    monkeypatch.setattr(views, "validate_rfid_value", lambda *_args, **_kwargs: {"rfid": "scan_next"})

    factory = RequestFactory()
    get_request = factory.get(reverse("rfid-scan-next"))
    get_request.user = AnonymousUser()

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 200
    assert json.loads(get_response.content) == {"rfid": "scan_next"}

    post_request = factory.post(
        reverse("rfid-scan-next"),
        data=json.dumps({"rfid": "deadbeef"}),
        content_type="application/json",
    )
    post_request.user = AnonymousUser()

    post_response = views.scan_next(post_request)

    assert post_response.status_code == 200
    assert json.loads(post_response.content) == {"rfid": "scan_next"}
