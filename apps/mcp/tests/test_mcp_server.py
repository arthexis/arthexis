"""Regression tests for MCP server integration with operational tools."""

from __future__ import annotations

import io
import json

import pytest

from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.mcp.models import McpApiKey
from apps.mcp.server import (
    ArthexisMCPServer,
    AuthenticatedMcpKey,
    McpAuthorizationError,
    McpProtocolError,
)
from apps.mcp.tools import McpToolDefinition, list_tools



def test_list_tools_includes_graphql_and_whoami() -> None:
    """Tool discovery should include non-CLI operational MCP tools."""

    tools = list_tools()

    assert "arthexis.graphql.query" in tools
    assert "arthexis.auth.whoami" in tools


def test_list_tools_respects_deny_filter() -> None:
    """Tool discovery should support deny-list filtering."""

    tools = list_tools(deny={"arthexis.auth.whoami"})

    assert "arthexis.graphql.query" in tools
    assert "arthexis.auth.whoami" not in tools


@pytest.mark.django_db
def test_mcp_tools_list_returns_registered_tools() -> None:
    """The MCP tools/list method should expose registered operational tools."""

    server = ArthexisMCPServer(allow={"arthexis.auth.whoami"})

    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )

    assert response is not None
    tools = response["result"]["tools"]
    assert tools == [
        {
            "name": "arthexis.auth.whoami",
            "description": "Return profile details for the authenticated MCP API key owner.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "description": "MCP API key generated via manage.py mcp key create.",
                    }
                },
                "required": ["api_key"],
                "additionalProperties": False,
            },
        }
    ]


@pytest.mark.django_db
def test_mcp_tools_call_executes_whoami_tool() -> None:
    """The MCP tools/call endpoint should execute non-CLI tools."""

    user = get_user_model().objects.create_user(username="alice")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    server = ArthexisMCPServer(allow={"arthexis.auth.whoami"})
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "arthexis.auth.whoami",
                "arguments": {"api_key": plain_key},
            },
        }
    )

    assert response is not None
    payload = response["result"]
    result_data = json.loads(payload["content"][0]["text"])
    assert result_data["username"] == "alice"
    assert result_data["groups"] == []


@pytest.mark.django_db
def test_mcp_tools_call_executes_graphql_query() -> None:
    """The GraphQL MCP tool should execute in-process schema queries."""

    user = get_user_model().objects.create_user(username="graphql-user")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    server = ArthexisMCPServer(allow={"arthexis.graphql.query"})
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "arthexis.graphql.query",
                "arguments": {
                    "api_key": plain_key,
                    "query": "query { __typename }",
                },
            },
        }
    )

    assert response is not None
    payload = response["result"]
    result_data = json.loads(payload["content"][0]["text"])
    assert result_data == {"data": {"__typename": "Query"}}


@pytest.mark.django_db
def test_mcp_tools_call_handles_mcptoolerror_as_tool_error_result() -> None:
    """McpToolError raised by a tool should be returned as a tool-result error payload."""

    user = get_user_model().objects.create_user(username="tool-error-user")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    server = ArthexisMCPServer(allow={"arthexis.graphql.query"})
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "arthexis.graphql.query",
                "arguments": {
                    "api_key": plain_key,
                    "query": "query { __typename }",
                    "variables": "not-an-object",
                },
            },
        }
    )

    assert response is not None
    payload = response["result"]
    assert payload["isError"] is True
    assert payload["content"][0]["text"].startswith("Invalid tool arguments:")


@pytest.mark.django_db
def test_mcp_tools_call_strips_api_key_before_handler() -> None:
    """Raw api_key should not be forwarded to tool handlers."""

    user = get_user_model().objects.create_user(username="bob")
    _api_key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    captured_arguments: dict[str, object] = {}

    def _inspect_handler(*, arguments: dict[str, object], user) -> dict[str, object]:
        captured_arguments.update(arguments)
        return {"username": user.get_username()}

    server = ArthexisMCPServer()
    server.__dict__["_tools"] = {
        "test.inspect": McpToolDefinition(
            name="test.inspect",
            description="inspect",
            input_schema={"type": "object"},
            handler=_inspect_handler,
        )
    }

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "test.inspect",
                "arguments": {"api_key": plain_key, "echo": "hello"},
            },
        }
    )

    assert response is not None
    assert captured_arguments == {"echo": "hello"}


def test_mcp_management_command_accepts_allow_and_deny(monkeypatch) -> None:
    """The mcp server subcommand should pass allow and deny tool filters."""

    captured: dict[str, set[str]] = {}

    def _fake_run_stdio_server(
        *, allow: set[str] | None = None, deny: set[str] | None = None
    ) -> None:
        captured["allow"] = allow or set()
        captured["deny"] = deny or set()

    monkeypatch.setattr(
        "apps.mcp.management.commands._mcp_command_logic.run_stdio_server",
        _fake_run_stdio_server,
    )

    call_command(
        "mcp",
        "server",
        "--allow",
        "arthexis.graphql.query,arthexis.auth.whoami",
        "--deny",
        "arthexis.auth.whoami",
    )

    assert captured["allow"] == {"arthexis.graphql.query", "arthexis.auth.whoami"}
    assert captured["deny"] == {"arthexis.auth.whoami"}


def test_mcp_rejects_non_object_payload() -> None:
    """Non-object JSON payloads should return a protocol error."""

    server = ArthexisMCPServer(allow={"arthexis.auth.whoami"})

    with pytest.raises(McpProtocolError) as exc_info:
        server.handle_request([])

    assert "payload must be an object" in str(exc_info.value)


@pytest.mark.django_db
def test_mcp_tools_call_rejects_when_missing_api_key() -> None:
    """tools/call should fail when no API key argument is provided."""

    server = ArthexisMCPServer(allow={"arthexis.auth.whoami"})

    with pytest.raises(McpProtocolError) as exc_info:
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "arthexis.auth.whoami",
                    "arguments": {},
                },
            }
        )

    assert "api_key" in str(exc_info.value)


@pytest.mark.django_db
def test_authenticate_key_uses_secret_key_fallbacks_and_upgrades_hash(settings) -> None:
    """Keys derived with a fallback secret should still authenticate and migrate."""

    settings.SECRET_KEY = "current-secret"
    settings.SECRET_KEY_FALLBACKS = ["previous-secret"]

    user = get_user_model().objects.create_user(username="fallback-secret-user")
    key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    fallback_hash = McpApiKey.objects._build_key_hash(
        plain_key, secret_key="previous-secret"
    )
    key.key_hash = fallback_hash
    key.save(update_fields=["key_hash"])

    authenticated = McpApiKey.objects.authenticate_key(plain_key)

    assert authenticated is not None
    key.refresh_from_db()
    assert key.key_hash != fallback_hash
    assert key.key_hash == McpApiKey.objects._build_key_hash(plain_key)


@pytest.mark.django_db
def test_authenticate_key_migrates_legacy_sha256_hash() -> None:
    """Authenticating a legacy key should transparently upgrade its stored hash."""

    user = get_user_model().objects.create_user(username="legacy-hash-user")
    key, plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    legacy_hash = McpApiKey.objects._build_legacy_hash(plain_key)
    key.key_hash = legacy_hash
    key.save(update_fields=["key_hash"])

    authenticated = McpApiKey.objects.authenticate_key(plain_key)

    assert authenticated is not None
    key.refresh_from_db()
    assert key.key_hash != legacy_hash
    assert key.key_hash == McpApiKey.objects._build_key_hash(plain_key)


@pytest.mark.django_db
def test_security_group_check_rejects_user_without_membership() -> None:
    """Group-protected tools should reject users outside required groups."""

    user = get_user_model().objects.create_user(username="carol")
    key, _plain_key = McpApiKey.objects.create_for_user(user=user, label="tests")

    restricted_tool = McpToolDefinition(
        name="restricted.tool",
        description="restricted",
        input_schema={"type": "object"},
        handler=lambda **_kwargs: {},
        security_groups=frozenset({"ops"}),
    )

    with pytest.raises(McpAuthorizationError) as exc_info:
        ArthexisMCPServer._assert_tool_group_access(
            tool=restricted_tool,
            authenticated_key=AuthenticatedMcpKey(key=key, user=user),
        )

    assert "security groups" in str(exc_info.value)


@pytest.mark.django_db
def test_mcp_key_create_subcommand_generates_api_key() -> None:
    """The mcp key create subcommand should create user-scoped API keys."""

    user = get_user_model().objects.create_user(username="key-user")
    del user

    out = io.StringIO()
    call_command(
        "mcp",
        "key",
        "create",
        "--username",
        "key-user",
        "--label",
        "tests",
        "--expires-in-days",
        "0",
        stdout=out,
    )

    output = out.getvalue()
    assert "MCP API key created." in output
    assert "username=key-user" in output
    assert "expires_at=never" in output



def test_legacy_mcp_commands_are_removed() -> None:
    """Legacy MCP command names should no longer be discoverable."""

    from django.core.management.base import CommandError

    with pytest.raises(CommandError) as mcp_server_error:
        call_command("mcp_server")
    with pytest.raises(CommandError) as key_error:
        call_command("create_mcp_api_key")

    assert "Unknown command" in str(mcp_server_error.value)
    assert "Unknown command" in str(key_error.value)
