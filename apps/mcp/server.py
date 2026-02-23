from __future__ import annotations

"""Minimal MCP-compatible JSON-RPC server for Arthexis operational tools."""

import json
import logging
import os
import sys
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from apps.mcp.models import McpApiKey
from apps.mcp.tools import McpToolDefinition, McpToolError, list_tools, serialize_tool_result

logger = logging.getLogger(__name__)


class McpProtocolError(ValueError):
    """Raised when an incoming JSON-RPC payload is invalid."""


class McpAuthenticationError(PermissionError):
    """Raised when an MCP request does not include a valid API key."""


class McpAuthorizationError(PermissionError):
    """Raised when an authenticated key lacks tool group permissions."""


@dataclass(frozen=True)
class AuthenticatedMcpKey:
    """Authenticated key payload used during tool authorization."""

    key: McpApiKey
    user: AbstractBaseUser


class ArthexisMCPServer:
    """Expose Arthexis operations as MCP tools over JSON-RPC."""

    def __init__(self, *, allow: set[str] | None = None, deny: set[str] | None = None) -> None:
        self._allow = allow or set()
        self._deny = deny or set()

    @cached_property
    def _tools(self) -> dict[str, McpToolDefinition]:
        return list_tools(allow=self._allow, deny=self._deny)

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
    def _assert_tool_group_access(
        *, tool: McpToolDefinition, authenticated_key: AuthenticatedMcpKey
    ) -> None:
        """Validate group access rules for a tool definition."""

        if not tool.security_groups:
            return

        user_group_names = set(authenticated_key.user.groups.values_list("name", flat=True))
        if tool.security_groups.isdisjoint(user_group_names):
            required = ", ".join(sorted(tool.security_groups))
            raise McpAuthorizationError(
                f"Tool requires one of these security groups: {required}"
            )

    def _call_tool(self, *, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered tool and return MCP content payload."""

        tool = self._tools.get(tool_name)
        if tool is None:
            raise McpToolError(f"Tool '{tool_name}' is not available.")

        api_key = self._extract_api_key(arguments)
        authenticated_key = self._authenticate(api_key)
        self._assert_tool_group_access(tool=tool, authenticated_key=authenticated_key)
        handler_arguments = {key: value for key, value in arguments.items() if key != "api_key"}

        try:
            tool_result = tool.handler(arguments=handler_arguments, user=authenticated_key.user)
        except (TypeError, ValueError, McpToolError) as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid tool arguments: {exc}"}],
                "isError": True,
            }

        return {
            "content": [{"type": "text", "text": serialize_tool_result(tool_result)}],
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
                        "name": "arthexis-ops-tools",
                        "version": os.getenv("ARTHEXIS_VERSION", "dev"),
                    },
                    "capabilities": {"tools": {}},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            tools = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in self._tools.values()
            ]
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not name:
                raise McpProtocolError("tools/call requires a non-empty string 'name'.")
            if not isinstance(arguments, dict):
                raise McpProtocolError("tools/call arguments must be an object.")
            result = self._call_tool(tool_name=name, arguments=arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


# Backward compatible alias for import stability.
DjangoCommandMCPServer = ArthexisMCPServer

MAX_STDIN_LINE_BYTES = 1024 * 1024


def run_stdio_server(*, allow: set[str] | None = None, deny: set[str] | None = None) -> None:
    """Run the MCP server on stdio until EOF."""

    server = ArthexisMCPServer(allow=allow, deny=deny)

    while True:
        payload: Any = None
        raw_bytes = sys.stdin.buffer.readline(MAX_STDIN_LINE_BYTES + 1)
        if raw_bytes == b"":
            break

        if len(raw_bytes) > MAX_STDIN_LINE_BYTES and not raw_bytes.endswith(b"\n"):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            print(json.dumps(response), flush=True)
            continue

        raw = raw_bytes.decode("utf-8", errors="replace").strip()

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
        except McpAuthenticationError as exc:
            logger.warning("MCP authentication failed: %s", exc)
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        except McpAuthorizationError as exc:
            logger.warning("MCP authorization denied: %s", exc)
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": str(exc)},
            }
        except McpToolError as exc:
            logger.exception("MCP request failed due to tool registration/execution error.")
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32002, "message": str(exc)},
            }
        except Exception:
            logger.exception("MCP request failed due to unexpected error.")
            request_id = payload.get("id") if isinstance(payload, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error"},
            }

        print(json.dumps(response), flush=True)
