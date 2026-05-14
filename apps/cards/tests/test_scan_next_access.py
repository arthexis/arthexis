from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from apps.cards import views
from apps.cards.login_poll import (
    RFID_LOGIN_POLL_QUERY_PARAM,
    RFID_LOGIN_POLL_SESSION_KEY,
)
from apps.cards.models import RFIDAttempt

pytestmark = [pytest.mark.django_db]


def _make_node(role_name: str) -> SimpleNamespace:
    return SimpleNamespace(role=SimpleNamespace(name=role_name))


def test_scan_next_anonymous_html_get_redirects_for_non_control_role(monkeypatch):
    """scan_next uses Node.get_local; role.name == "Control" allows anonymous GET."""
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


def test_scan_next_blocks_anonymous_get_for_control_role(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)

    factory = RequestFactory()
    get_request = factory.get(reverse("rfid-scan-next"))
    get_request.user = AnonymousUser()

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 302


def test_scan_next_blocks_anonymous_json_get_for_control_role(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)
    RFIDAttempt.objects.create(
        rfid="SCAN_NEXT_JSON",
        status=RFIDAttempt.Status.SCANNED,
        source=RFIDAttempt.Source.SERVICE,
        payload={"rfid": "SCAN_NEXT_JSON"},
    )

    factory = RequestFactory()
    get_request = factory.get(
        reverse("rfid-scan-next"),
        HTTP_ACCEPT="application/json",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    get_request.user = AnonymousUser()

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 401
    assert json.loads(get_response.content) == {"error": "Authentication required"}


def test_scan_next_allows_session_scoped_login_poll_for_control_role(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)
    RFIDAttempt.objects.create(
        rfid="SCAN_NEXT_LOGIN",
        status=RFIDAttempt.Status.SCANNED,
        source=RFIDAttempt.Source.SERVICE,
        payload={"rfid": "SCAN_NEXT_LOGIN"},
    )

    factory = RequestFactory()
    token = "login-poll-token"
    get_request = factory.get(
        reverse("rfid-scan-next"),
        {RFID_LOGIN_POLL_QUERY_PARAM: token},
        HTTP_ACCEPT="application/json",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    get_request.user = AnonymousUser()
    get_request.session = {RFID_LOGIN_POLL_SESSION_KEY: token}

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 200
    assert json.loads(get_response.content)["rfid"] == "SCAN_NEXT_LOGIN"


def test_scan_next_blocks_login_poll_with_mismatched_session_token(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)

    factory = RequestFactory()
    get_request = factory.get(
        reverse("rfid-scan-next"),
        {RFID_LOGIN_POLL_QUERY_PARAM: "request-token"},
        HTTP_ACCEPT="application/json",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    get_request.user = AnonymousUser()
    get_request.session = {RFID_LOGIN_POLL_SESSION_KEY: "session-token"}

    get_response = views.scan_next(get_request)

    assert get_response.status_code == 401
    assert json.loads(get_response.content) == {"error": "Authentication required"}


def test_scan_next_blocks_anonymous_post_for_control_role(monkeypatch):
    node = _make_node("Control")
    monkeypatch.setattr(views.Node, "get_local", lambda: node)

    factory = RequestFactory()
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
