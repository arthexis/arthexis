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
        return self._payload


def test_node_login_sets_authenticated_session():
    session = requests.Session()
    session.get = Mock(
        return_value=_Response(
            text='<form><input name="csrfmiddlewaretoken" value="token123"/></form>'
        )
    )

    def _fake_post(*args, **kwargs):
        session.cookies.set("sessionid", "abc123")
        return _Response()

    session.post = Mock(side_effect=_fake_post)

    node = Node("https://example.com", session=session)
    node.login("user", "pass")

    assert node.is_authenticated is True


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


def test_login_raises_when_csrf_token_missing():
    session = requests.Session()
    session.get = Mock(return_value=_Response(text="<html></html>"))
    node = Node("https://example.com", session=session)

    with pytest.raises(ArthexisNotebookError):
        node.login("user", "pass")
