import io

import pytest
from django.core.management import get_commands, load_command_class


@pytest.mark.parametrize(
    "command_name,kwargs,expected_action",
    [
        ("register-node", {"token": "abc"}, "register"),
        (
            "register-node-curl",
            {"upstream": "https://example.com", "local_base": "https://local:8888", "token": "x"},
            "register_curl",
        ),
        (
            "lan-find-node",
            {"ports": "8888", "timeout": 1.0, "max_hosts": 5, "interfaces": "eth0"},
            "discover",
        ),
        ("update-peer-nodes", {}, "peers"),
        ("check_nodes", {}, "check"),
        ("registration_ready", {}, "ready"),
    ],
)
def test_legacy_wrapper_dispatches_to_node(monkeypatch, command_name, kwargs, expected_action):
    app_name = get_commands()[command_name]
    command = load_command_class(app_name, command_name)
    command.stdout = io.StringIO()

    calls = []

    def fake_call_command(name, *args, **inner_kwargs):
        calls.append((name, args, inner_kwargs))

    monkeypatch.setitem(command.handle.__globals__, "call_command", fake_call_command)

    command.handle(**kwargs)

    assert calls
    name, args, inner_kwargs = calls[0]
    assert name == "node"
    assert args[0] == expected_action
    assert "deprecated" in command.stdout.getvalue().lower()
