import base64
import json
from pathlib import Path

import pytest
from django.conf import settings
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


def test_load_sibling_info_from_path_accepts_banner_prefix(tmp_path, monkeypatch):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()

    monkeypatch.setattr(
        command,
        "_run_sibling_registration_subprocess",
        lambda *_args, **_kwargs: "Arthexis 1.0.0\n{\"hostname\":\"sibling-node\"}",
    )

    result = command._load_sibling_info_from_path(sibling_path)

    assert result == {"hostname": "sibling-node"}


def test_load_sibling_info_from_path_requires_json_object(tmp_path, monkeypatch):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()

    monkeypatch.setattr(
        command,
        "_run_sibling_registration_subprocess",
        lambda *_args, **_kwargs: "Arthexis 1.0.0\n[]",
    )

    with pytest.raises(CommandError, match="must be a JSON object"):
        command._load_sibling_info_from_path(sibling_path)


def test_node_register_path_mode_uses_base_dir_for_reciprocal_path(monkeypatch, tmp_path):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()
    (sibling_path / "manage.py").write_text("# sibling manage.py\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        command,
        "_load_sibling_info_from_path",
        lambda *_args, **_kwargs: {
            "hostname": "sibling-node",
            "address": "127.0.0.1",
            "port": 9999,
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "public_key": "PUB",
            "features": ["mesh"],
        },
    )
    monkeypatch.setattr(
        command, "_register_host_locally", lambda *_args, **_kwargs: None
    )

    def _capture_subprocess(path, manage_args):
        captured["path"] = path
        captured["manage_args"] = manage_args
        return ""

    monkeypatch.setattr(
        command, "_run_sibling_registration_subprocess", _capture_subprocess
    )

    command.handle(
        action="register",
        token="",
        sibling_path=str(sibling_path),
        no_reciprocal=False,
    )

    assert captured["path"] == sibling_path.resolve()
    reciprocal_index = captured["manage_args"].index("--path") + 1
    expected = Path(settings.BASE_DIR).resolve().as_posix()
    assert captured["manage_args"][reciprocal_index] == expected


def test_node_register_path_mode_rejects_local_install_path(monkeypatch):
    command = _load_node_command()

    with pytest.raises(CommandError, match="different installation"):
        command.handle(
            action="register",
            token="",
            sibling_path=str(settings.BASE_DIR),
            no_reciprocal=True,
        )


def test_run_sibling_registration_subprocess_wraps_os_error(monkeypatch, tmp_path):
    command = _load_node_command()
    sibling_path = tmp_path / "sibling"
    sibling_path.mkdir()

    def _raise_os_error(*_args, **_kwargs):
        raise OSError("launcher not found")

    monkeypatch.setattr("subprocess.run", _raise_os_error)

    with pytest.raises(CommandError, match="Unable to run sibling command"):
        command._run_sibling_registration_subprocess(sibling_path, ["node", "info_json"])
