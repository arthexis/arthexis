"""Tests for node command subcommands migrated from legacy commands."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError

from apps.nodes.models import Node


def _load_node_command():
    app_name = get_commands()["node"]
    return load_command_class(app_name, "node")


def test_node_message_broadcast(monkeypatch):
    """The node message action should forward all broadcast kwargs."""

    command = _load_node_command()
    command.stdout = io.StringIO()
    captured: dict[str, object] = {}

    def fake_broadcast(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("apps.nodes.management.commands.node.NetMessage.broadcast", fake_broadcast)

    command.handle(
        action="message",
        subject="Hello",
        body="World",
        reach="operator",
        seen=["a", "b"],
        lcd_channel_type="high",
        lcd_channel_num=2,
    )

    assert captured == {
        "subject": "Hello",
        "body": "World",
        "reach": "operator",
        "seen": ["a", "b"],
        "lcd_channel_type": "high",
        "lcd_channel_num": 2,
    }
    assert "Net message broadcast" in command.stdout.getvalue()


@pytest.mark.django_db
def test_node_purge_nodes_warns_for_anonymous_nodes():
    """The node purge_nodes action should preserve anonymous nodes by default."""

    Node.objects.create(hostname="dupe", mac_address="")
    anonymous = Node.objects.create(hostname="", mac_address="")

    command = _load_node_command()
    command.stdout = io.StringIO()

    command.handle(action="purge_nodes", remove_anonymous=False)

    remaining_ids = set(Node.objects.values_list("id", flat=True))
    assert anonymous.id in remaining_ids
    assert len(remaining_ids) == 2
    output = command.stdout.getvalue()
    assert "No nodes purged." in output
    assert "Skipped nodes missing deduplication keys" in output


@pytest.mark.django_db
def test_node_purge_net_messages_noop_output():
    """The node purge_net_messages action should report empty state clearly."""

    command = _load_node_command()
    command.stdout = io.StringIO()

    command.handle(action="purge_net_messages")

    assert "No net messages found." in command.stdout.getvalue()


def test_node_screenshot_rejects_invalid_argument_combo():
    """The node screenshot action should enforce mutually exclusive args."""

    command = _load_node_command()
    with pytest.raises(CommandError, match="--local cannot be used together with a URL"):
        command.handle(action="screenshot", url="https://example.com", local=True, freq=None)


@pytest.mark.parametrize(
    ("legacy", "action", "args", "kwargs"),
    [
        ("message", "message", ["Subject", "Body"], {"reach": "ops"}),
        ("purge_nodes", "purge_nodes", [], {"remove_anonymous": True}),
        ("purge_net_messages", "purge_net_messages", [], {}),
        ("screenshot", "screenshot", ["https://example.com"], {"freq": 5, "local": False}),
    ],
)
def test_legacy_command_wrappers_delegate(monkeypatch, legacy, action, args, kwargs):
    """Legacy commands should print deprecation warnings and call node subcommands."""

    stdout = io.StringIO()
    calls: list[tuple[tuple, dict]] = []

    def fake_call_command(*inner_args, **inner_kwargs):
        calls.append((inner_args, inner_kwargs))

    monkeypatch.setattr("apps.nodes.management.commands.message.call_command", fake_call_command)
    monkeypatch.setattr("apps.nodes.management.commands.purge_nodes.call_command", fake_call_command)
    monkeypatch.setattr(
        "apps.nodes.management.commands.purge_net_messages.call_command", fake_call_command
    )
    monkeypatch.setattr("apps.nodes.management.commands.screenshot.call_command", fake_call_command)

    call_command(legacy, *args, stdout=stdout, **kwargs)

    assert "DEPRECATED:" in stdout.getvalue()
    assert calls, "Wrapper did not delegate to node command"
    forwarded_args, forwarded_kwargs = calls[-1]
    assert forwarded_args[0] == "node"
    assert forwarded_args[1] == action


def test_node_screenshot_returns_path(monkeypatch):
    """The node screenshot action should return the captured path."""

    command = _load_node_command()
    command.stdout = io.StringIO()
    monkeypatch.setattr(
        "apps.nodes.management.commands.node.capture_and_save_screenshot",
        lambda **_: Path("shots/example.png"),
    )

    result = command.handle(action="screenshot", url=None, local=False, freq=None)

    assert result == "shots/example.png"
    assert "shots/example.png" in command.stdout.getvalue()


@pytest.mark.django_db
def test_node_refresh_features_action_refreshes_local_node(monkeypatch):
    """The node refresh_features action should refresh local auto-managed features."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    calls: list[int] = []

    def fake_refresh_local_node_features():
        calls.append(node.pk)
        return node

    command = _load_node_command()
    command.stdout = io.StringIO()
    monkeypatch.setattr(
        "apps.nodes.management.commands.node.refresh_local_node_features",
        fake_refresh_local_node_features,
    )

    command.handle(action="refresh_features")

    assert calls == [node.pk]
    assert "Successfully refreshed features." in command.stdout.getvalue()
