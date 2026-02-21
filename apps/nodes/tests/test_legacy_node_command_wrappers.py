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
    command.stderr = io.StringIO()

    calls = []

    def fake_call_command(name, *args, **inner_kwargs):
        calls.append((name, args, inner_kwargs))

    monkeypatch.setitem(command.handle.__globals__, "call_command", fake_call_command)

    command.handle(**kwargs)

    assert calls
    name, args, inner_kwargs = calls[0]
    assert name == "node"
    assert args[0] == expected_action
    assert "deprecated" in command.stderr.getvalue().lower()


def test_register_node_curl_wrapper_forwards_output_streams_and_flags(monkeypatch):
    app_name = get_commands()["register-node-curl"]
    command = load_command_class(app_name, "register-node-curl")
    command.stdout = io.StringIO()
    command.stderr = io.StringIO()

    calls = []

    def fake_call_command(name, *args, **inner_kwargs):
        calls.append((name, args, inner_kwargs))

    monkeypatch.setitem(command.handle.__globals__, "call_command", fake_call_command)

    command.handle(
        upstream="https://example.com",
        local_base="https://local:8888",
        token="x",
        skip_checks=True,
        verbosity=2,
        force_color=True,
        no_color=False,
        traceback=True,
        stdout=command.stdout,
        stderr=command.stderr,
    )

    assert calls
    _, args, inner_kwargs = calls[0]
    assert args == ("register_curl", "https://example.com")
    assert inner_kwargs["local_base"] == "https://local:8888"
    assert inner_kwargs["token"] == "x"
    assert inner_kwargs["stdout"] is command.stdout
    assert inner_kwargs["stderr"] is command.stderr
    assert inner_kwargs["skip_checks"] is True
    assert inner_kwargs["verbosity"] == 2
    assert inner_kwargs["force_color"] is True
    assert inner_kwargs["traceback"] is True
