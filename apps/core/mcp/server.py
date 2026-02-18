from __future__ import annotations

"""Minimal MCP-compatible JSON-RPC server for selected Django commands."""

import json
import os
from dataclasses import dataclass
from functools import cached_property
from io import StringIO
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import McpApiKey

from .remote_commands import (
    RemoteCommandError,
    RemoteCommandMetadata,
    discover_remote_commands,
)


class McpProtocolError(ValueError):
    """Raised when an incoming JSON-RPC payload is invalid."""

class McpAuthenticationError(PermissionError):
    """Raised when an MCP request does not include a valid API key."""


class McpAuthorizationError(PermissionError):
    """Raised when an authenticated key lacks command group permissions."""


@dataclass(frozen=True)
class AuthenticatedMcpKey:
    """Authenticated key payload used during command authorization."""

    key: McpApiKey
    user: AbstractBaseUser


class DjangoCommandMCPServer:
    """Expose selected Django management commands as MCP tools over JSON-RPC."""

    def __init__(
        self, *, allow: set[str] | None = None, deny: set[str] | None = None
    ) -> None:
        self._allow = allow or set()
        self._deny = deny or set()

    @cached_property
    def _tools(self) -> dict[str, dict[str, Any]]:
        discovered = discover_remote_commands(allow=self._allow, deny=self._deny)
        tools: dict[str, dict[str, Any]] = {}
        for name, metadata in discovered.items():
            tool_name = f"django.command.{name}"
            tools[tool_name] = {
                "_metadata": metadata,
                "name": tool_name,
                "description": metadata.description,
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
        return tools

    @staticmethod
    def _extract_api_key(arguments: dict[str, Any]) -> str:
        """Extract and normalize the API key from tool call arguments."""

        api_key = arguments.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise McpProtocolError("tools/call requires a non-empty 'api_key'.")
        return api_key.strip()

    @staticmethod
    def _authenticate(api_key: str) -> AuthenticatedMcpKey:
        """Authenticate and return the API key model and owning user."""

        key_obj = McpApiKey.objects.authenticate_key(api_key)
        if key_obj is None:
            raise McpAuthenticationError("Invalid or expired MCP API key.")
        key_obj.mark_used()
        return AuthenticatedMcpKey(key=key_obj, user=key_obj.user)

    @staticmethod
    def _assert_command_group_access(
        *, metadata: RemoteCommandMetadata, authenticated_key: AuthenticatedMcpKey
    ) -> None:
        """Validate group access rules for a remote command."""

        if not metadata.security_groups:
            return

        user_group_names = set(
            authenticated_key.user.groups.values_list("name", flat=True)
        )
        if metadata.security_groups.isdisjoint(user_group_names):
            required = ", ".join(sorted(metadata.security_groups))
            raise McpAuthorizationError(
                f"Command requires one of these security groups: {required}"
            )

    @staticmethod
    def _result_text(stdout: str, stderr: str) -> str:
        chunks = []
        if stdout.strip():
            chunks.append(f"stdout:\n{stdout.rstrip()}")
        if stderr.strip():
            chunks.append(f"stderr:\n{stderr.rstrip()}")
        return "\n\n".join(chunks) or "Command completed without output."

    def _call_tool(
        self, *, tool_name: str, args: list[str], api_key: str
    ) -> dict[str, Any]:
        tools = self._tools
        tool = tools.get(tool_name)
        if tool is None:
            raise RemoteCommandError(f"Tool '{tool_name}' is not available.")

        metadata = tool.get("_metadata")
        if not isinstance(metadata, RemoteCommandMetadata):
            raise RemoteCommandError(f"Tool '{tool_name}' metadata is unavailable.")

        authenticated_key = self._authenticate(api_key)
        self._assert_command_group_access(
            metadata=metadata, authenticated_key=authenticated_key
        )

        command_name = tool_name.removeprefix("django.command.")
        stdout = StringIO()
        stderr = StringIO()

        try:
            call_command(command_name, *args, stdout=stdout, stderr=stderr)
        except CommandError as exc:
            return {
                "content": [{"type": "text", "text": f"CommandError: {exc}"}],
                "isError": True,
            }
        except (TypeError, ValueError) as exc:
            return {
                "content": [
                    {"type": "text", "text": f"Invalid command arguments: {exc}"}
                ],
                "isError": True,
            }
        except SystemExit as exc:
            return {
                "content": [{"type": "text", "text": f"Command exited: {exc}"}],
                "isError": True,
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": self._result_text(stdout.getvalue(), stderr.getvalue()),
                }
            ]
        }

    def handle_request(self, payload: Any) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request and return its response payload."""

        if not isinstance(payload, dict):
            raise McpProtocolError("JSON-RPC payload must be an object.")

        if payload.get("jsonrpc") != "2.0":
            raise McpProtocolError("Only JSON-RPC 2.0 payloads are supported.")

        method = payload.get("method")
        request_id = payload.get("id")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise McpProtocolError("Request params must be an object.")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "arthexis-django-commands",
                        "version": os.getenv("ARTHEXIS_VERSION", "dev"),
                    },
                    "capabilities": {"tools": {}},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            tools = []
            for tool in self._tools.values():
                tool_payload = {k: v for k, v in tool.items() if k != "_metadata"}
                tools.append(tool_payload)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not name:
                raise McpProtocolError("tools/call requires a non-empty string 'name'.")
            if not isinstance(arguments, dict):
                raise McpProtocolError("tools/call arguments must be an object.")

            args = arguments.get("args", [])
            if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
                raise McpProtocolError("'args' must be an array of strings.")

            api_key = self._extract_api_key(arguments)
            result = self._call_tool(tool_name=name, args=args, api_key=api_key)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def run_stdio_server(
    *, allow: set[str] | None = None, deny: set[str] | None = None
) -> None:
    """Run the MCP server on stdio until EOF."""

    server = DjangoCommandMCPServer(allow=allow, deny=deny)

    # input() raises EOFError when stdin closes; that is expected shutdown behavior.
    while True:
        payload: Any = None
        try:
            raw = input()
        except EOFError:
            break
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            response = server.handle_request(payload)
            if response is None:
                continue
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        except McpProtocolError as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32600, "message": str(exc)},
            }
        except (RemoteCommandError, McpAuthenticationError) as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        except McpAuthorizationError as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": str(exc)},
            }

        print(json.dumps(response), flush=True)
