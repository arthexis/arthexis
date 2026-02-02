import base64
import json
import io

import pytest
from django.core.management import get_commands, load_command_class
from django.core.management.base import CommandError


def _encode_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _load_command():
    app_name = get_commands()["register-node"]
    return load_command_class(app_name, "register-node")


def test_register_node_command_requires_https_urls():
    token = _encode_token(
        {
            "register": "http://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_command()

    with pytest.raises(CommandError, match="Host registration URL must use https"):
        command.handle(token=token)


def test_register_node_command_warns_when_https_not_required(monkeypatch):
    token = _encode_token(
        {
            "register": "https://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_command()
    command.stdout = io.StringIO()

    def fake_request_json(session, url, *, method="get", json_body=None):
        if method == "post":
            return {"id": 123}
        return {"base_site_requires_https": False}

    def fake_load_local_info():
        return {"base_site_requires_https": False}

    monkeypatch.setattr(command, "_request_json", fake_request_json)
    monkeypatch.setattr(command, "_load_local_info", fake_load_local_info)
    monkeypatch.setattr(command, "_register_host_locally", lambda payload: None)

    command.handle(token=token)

    output = command.stdout.getvalue()
    assert "Host node is not configured to require HTTPS" in output
    assert "Local node is not configured to require HTTPS" in output
