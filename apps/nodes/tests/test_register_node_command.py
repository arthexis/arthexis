import base64
import json

import pytest
from django.core.management import get_commands, load_command_class
from django.core.management.base import CommandError


def _encode_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _load_node_command():
    app_name = get_commands()["node"]
    return load_command_class(app_name, "node")


def test_node_register_requires_https_urls():
    token = _encode_token(
        {
            "register": "http://example.com/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_node_command()

    with pytest.raises(CommandError, match="Host registration URL must use https"):
        command.handle(action="register", token=token)


def test_node_register_rejects_private_host_in_token():
    token = _encode_token(
        {
            "register": "https://127.0.0.1/nodes/register/",
            "info": "https://example.com/nodes/info/",
            "username": "user",
            "password": "pass",
        }
    )
    command = _load_node_command()

    with pytest.raises(CommandError, match="Host registration URL host must not resolve"):
        command.handle(action="register", token=token)


def test_node_register_path_mode_registers_sibling(monkeypatch, tmp_path):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()
    (sibling_path / "manage.py").write_text("# sibling manage.py\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        command,
        "_load_sibling_info_from_path",
        lambda path: {
            "hostname": "sibling-node",
            "address": "127.0.0.1",
            "port": 9999,
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "public_key": "PUB",
            "features": ["mesh"],
        },
    )
    monkeypatch.setattr(
        command,
        "_register_host_locally",
        lambda payload: captured.setdefault("payload", payload),
    )
    monkeypatch.setattr(
        command,
        "_run_sibling_registration_subprocess",
        lambda *args, **kwargs: captured.setdefault("reciprocal", True),
    )

    command.handle(
        action="register",
        token="",
        sibling_path=str(sibling_path),
        no_reciprocal=False,
    )

    assert captured["payload"]["hostname"] == "sibling-node"
    assert captured["payload"]["current_relation"] == "Sibling"
    assert captured["reciprocal"] is True


def test_node_register_path_mode_requires_manage_py(tmp_path):
    command = _load_node_command()
    missing_manage = tmp_path / "missing-manage"
    missing_manage.mkdir()

    with pytest.raises(CommandError, match="must contain manage.py"):
        command.handle(
            action="register",
            token="",
            sibling_path=str(missing_manage),
            no_reciprocal=True,
        )


def test_node_register_path_mode_rejects_token_and_path_together(tmp_path):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()
    (sibling_path / "manage.py").write_text("# sibling manage.py\n", encoding="utf-8")

    with pytest.raises(CommandError, match="either a token or --path"):
        command.handle(
            action="register",
            token="abc",
            sibling_path=str(sibling_path),
            no_reciprocal=False,
        )
