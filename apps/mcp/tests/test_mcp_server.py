"""Regression tests for MCP server integration with Django management commands."""

from __future__ import annotations

from io import StringIO

import pytest

from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.mcp.models import McpApiKey
from apps.mcp.remote_commands import RemoteCommandMetadata, discover_remote_commands
from apps.mcp.server import (
    AuthenticatedMcpKey,
    DjangoCommandMCPServer,
    McpAuthorizationError,
    McpProtocolError,
)

pytestmark = pytest.mark.critical


def test_discover_remote_commands_includes_decorated_commands() -> None:
    """Remote command discovery should return commands decorated for MCP use."""

    commands = discover_remote_commands()

    assert "uptime" in commands
    assert "redis" not in commands


@pytest.mark.django_db
def test_mcp_tools_list_returns_remote_tools() -> None:
    """The MCP tools/list method should expose decorated Django commands."""

    server = DjangoCommandMCPServer(allow={"uptime"})

    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )

    assert response is not None
    tools = response["result"]["tools"]
    assert tools == [
        {
            "name": "django.command.uptime",
            "description": "Display suite uptime and lock status.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Positional CLI arguments for the Django command.",
                        "default": [],
                    },
                    "api_key": {
                        "type": "string",
                        "description": "MCP API key generated via manage.py create_mcp_api_key.",
                    },
                },
                "required": ["api_key"],
                "additionalProperties": False,
            },
        }
    ]


@pytest.mark.django_db
def test_mcp_tools_call_executes_selected_command(monkeypatch) -> None:
    """The MCP tools/call endpoint should execute allowed Django commands."""

    def _fake_call_command(
        name: str, *args: str, stdout: StringIO, stderr: StringIO
    ) -> None:
        _ = stderr
        assert name == "uptime"
        assert args == ("--help",)
        stdout.write("ok")

    monkeypatch.setattr("apps.mcp.server.call_command", _fake_call_command)

    user = get_user_model().objects.create_user(username="alice")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    server = DjangoCommandMCPServer(allow={"uptime"})
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "django.command.uptime",
                "arguments": {"args": ["--help"], "api_key": plain_key},
            },
        }
    )

    assert response is not None
    payload = response["result"]
    assert payload["content"][0]["text"] == "stdout:\nok"


def test_mcp_management_command_accepts_allow_and_deny(monkeypatch) -> None:
    """The mcp_server management command should pass normalized filters."""

    captured: dict[str, set[str]] = {}

    def _fake_run_stdio_server(
        *, allow: set[str] | None = None, deny: set[str] | None = None
    ) -> None:
        captured["allow"] = allow or set()
        captured["deny"] = deny or set()

    monkeypatch.setattr(
        "apps.mcp.management.commands.mcp_server.run_stdio_server",
        _fake_run_stdio_server,
    )

    call_command("mcp_server", "--allow", "uptime,redis", "--deny", "redis")

    assert captured["allow"] == {"uptime", "redis"}
    assert captured["deny"] == {"redis"}


def test_mcp_rejects_non_object_payload() -> None:
    """Non-object JSON payloads should return a protocol error."""

    server = DjangoCommandMCPServer(allow={"uptime"})

    with pytest.raises(McpProtocolError) as exc_info:
        server.handle_request([])

    assert "payload must be an object" in str(exc_info.value)


@pytest.mark.django_db
def test_mcp_tools_call_handles_system_exit(monkeypatch) -> None:
    """SystemExit from call_command should be returned as tool error payload."""

    def _fake_call_command(
        _name: str, *_args: str, stdout: StringIO, stderr: StringIO
    ) -> None:
        _ = stderr
        stdout.write("usage")
        raise SystemExit(0)

    monkeypatch.setattr("apps.mcp.server.call_command", _fake_call_command)

    user = get_user_model().objects.create_user(username="bob")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    server = DjangoCommandMCPServer(allow={"uptime"})
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "django.command.uptime",
                "arguments": {"args": ["--help"], "api_key": plain_key},
            },
        }
    )

    assert response is not None
    assert response["id"] == 7
    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == "Command exited: 0"


@pytest.mark.django_db
def test_mcp_tools_call_rejects_when_missing_api_key() -> None:
    """tools/call should fail when no API key argument is provided."""

    server = DjangoCommandMCPServer(allow={"uptime"})

    with pytest.raises(McpProtocolError) as exc_info:
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "django.command.uptime",
                    "arguments": {"args": ["--help"]},
                },
            }
        )

    assert "api_key" in str(exc_info.value)


@pytest.mark.django_db
def test_security_group_check_rejects_user_without_membership() -> None:
    """Group-protected commands should reject authenticated users outside required groups."""

    user = get_user_model().objects.create_user(username="carol")
    key, _plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    with pytest.raises(McpAuthorizationError) as exc_info:
        DjangoCommandMCPServer._assert_command_group_access(
            metadata=RemoteCommandMetadata(
                command_name="uptime",
                description="desc",
                allow_remote=True,
                security_groups=frozenset({"ops"}),
            ),
            authenticated_key=AuthenticatedMcpKey(key=key, user=user),
        )

    assert "security groups" in str(exc_info.value)
