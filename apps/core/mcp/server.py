from __future__ import annotations

"""Minimal MCP-compatible JSON-RPC server for selected Django commands."""

import json
import os
from io import StringIO
from typing import Any

from django.core.management import call_command
from django.core.management.base import CommandError

from .remote_commands import (
    RemoteCommandError,
    RemoteCommandNotAllowedError,
    discover_remote_commands,
)


class McpProtocolError(ValueError):
    """Raised when an incoming JSON-RPC payload is invalid."""


class DjangoCommandMCPServer:
    """Expose selected Django management commands as MCP tools over JSON-RPC."""

    def __init__(
        self, *, allow: set[str] | None = None, deny: set[str] | None = None
    ) -> None:
        self._allow = allow or set()
        self._deny = deny or set()

    def _tools(self) -> dict[str, dict[str, Any]]:
        discovered = discover_remote_commands(allow=self._allow, deny=self._deny)
        tools: dict[str, dict[str, Any]] = {}
        for name, metadata in discovered.items():
            tool_name = f"django.command.{name}"
            tools[tool_name] = {
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
                        }
                    },
                    "additionalProperties": False,
                },
                "_command_name": name,
            }
        return tools

    @staticmethod
    def _result_text(stdout: str, stderr: str) -> str:
        chunks = []
        if stdout.strip():
            chunks.append(f"stdout:\n{stdout.rstrip()}")
        if stderr.strip():
            chunks.append(f"stderr:\n{stderr.rstrip()}")
        return "\n\n".join(chunks) or "Command completed without output."

    def _call_tool(self, tool_name: str, args: list[str]) -> dict[str, Any]:
        tools = self._tools()
        tool = tools.get(tool_name)
        if tool is None:
            raise RemoteCommandNotAllowedError(f"Tool '{tool_name}' is not available.")

        command_name = tool["_command_name"]
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

        return {
            "content": [
                {
                    "type": "text",
                    "text": self._result_text(stdout.getvalue(), stderr.getvalue()),
                }
            ]
        }

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request and return its response payload."""

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
            tools = list(self._tools().values())
            for tool in tools:
                tool.pop("_command_name", None)
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

            result = self._call_tool(name, args)
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

    while True:
        raw = input()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            response = server.handle_request(payload)
            if response is None:
                continue
        except (
            json.JSONDecodeError,
            McpProtocolError,
            RemoteCommandError,
            RemoteCommandNotAllowedError,
        ) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32602, "message": str(exc)},
            }

        print(json.dumps(response), flush=True)
