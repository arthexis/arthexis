from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from arthexis.notebook import ArthexisNotebookError, Node


class _Response:
    def __init__(self, *, text: str = "", payload=None, status_code: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_node_login_uses_admin_path_and_verifies_session():
    session = requests.Session()
    session.get = Mock(
        side_effect=[
            _Response(
                text='<form><input name="csrfmiddlewaretoken" value="token123"/></form>'
            ),
            _Response(),
        ]
    )

    session.post = Mock(return_value=_Response())

    node = Node("https://example.com", session=session, admin_path="control-panel/")
    node.login("user", "pass")

    assert node.is_authenticated is False
    session.get.assert_any_call("https://example.com/control-panel/login/", timeout=30.0)
    session.get.assert_any_call(
        "https://example.com/control-panel/",
        timeout=30.0,
        allow_redirects=False,
    )
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["data"]["next"] == "/control-panel/"


def test_login_rejects_stale_session_cookie_after_failed_auth_check():
    session = requests.Session()
    session.cookies.set("sessionid", "stale")
    session.get = Mock(
        side_effect=[
            _Response(
                text='<form><input name="csrfmiddlewaretoken" value="token123"/></form>'
            ),
            _Response(status_code=302),
        ]
    )
    session.post = Mock(return_value=_Response())
    node = Node("https://example.com", session=session)

    with pytest.raises(ArthexisNotebookError, match="Login failed"):
        node.login("user", "bad-password")


def test_node_login_sets_authenticated_session():
    session = requests.Session()
    session.get = Mock(
        side_effect=[
            _Response(
                text='<form><input name="csrfmiddlewaretoken" value="token123"/></form>'
            ),
            _Response(),
        ]
    )

    def _fake_post(*args, **kwargs):
        session.cookies.set("sessionid", "abc123")
        return _Response()

    session.post = Mock(side_effect=_fake_post)

    node = Node("https://example.com", session=session)
    node.login("user", "pass")

    assert node.is_authenticated is True


def test_extract_csrf_token_handles_attribute_order_and_quotes():
    html = (
        "<form>"
        "<input value='token123' class='x' name='csrfmiddlewaretoken'>"
        "</form>"
    )

    assert Node._extract_csrf_token(html) == "token123"


def test_extract_csrf_token_rejects_unexpected_characters():
    html = '<input name="csrfmiddlewaretoken" value="token-123">'

    assert Node._extract_csrf_token(html) is None


def test_login_wraps_csrf_fetch_errors():
    session = requests.Session()
    session.get = Mock(return_value=_Response(status_code=500))
    session.post = Mock()
    node = Node("https://example.com", session=session)

    with pytest.raises(ArthexisNotebookError, match="admin login page"):
        node.login("user", "pass")
    session.post.assert_not_called()


def test_chargers_returns_typed_wrappers():
    session = requests.Session()
    session.get = Mock(
        return_value=_Response(
            payload={"chargers": [{"charger_id": "SIM-CP-1", "status": "Charging"}]}
        )
    )

    chargers = Node("https://example.com", session=session).chargers()

    assert len(chargers) == 1
    assert chargers[0].charger_id == "SIM-CP-1"
    assert chargers[0].status == "Charging"


def test_charger_encodes_reserved_path_characters():
    session = requests.Session()
    session.get = Mock(return_value=_Response(payload={"charger_id": "CP/1 ?#"}))

    charger = Node("https://example.com", session=session).charger("CP/1 ?#")

    assert charger.charger_id == "CP/1 ?#"
    session.get.assert_called_once_with(
        "https://example.com/ocpp/chargers/CP%2F1%20%3F%23/",
        timeout=30.0,
    )


def test_login_raises_when_csrf_token_missing():
    session = requests.Session()
    session.get = Mock(return_value=_Response(text="<html></html>"))
    node = Node("https://example.com", session=session)

    with pytest.raises(ArthexisNotebookError):
        node.login("user", "pass")


def test_get_json_wraps_http_and_json_errors():
    session = requests.Session()
    session.get = Mock(return_value=_Response(status_code=500))
    node = Node("https://example.com", session=session)

    with pytest.raises(ArthexisNotebookError, match="Request failed"):
        node.chargers()

    session.get = Mock(return_value=_Response(payload=ValueError("bad json")))

    with pytest.raises(ArthexisNotebookError, match="Invalid JSON"):
        node.chargers()
