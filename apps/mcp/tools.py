"""MCP tool registry for Arthexis non-CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from apps.graphql.schema import schema


class McpToolError(RuntimeError):
    """Raised when a tool request cannot be completed safely."""


@dataclass(frozen=True)
class McpToolDefinition:
    """Metadata and implementation details for a registered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    security_groups: frozenset[str] = frozenset()


def _execute_graphql_query(
    *, query: str, variables: dict[str, Any] | None, operation_name: str | None, user
) -> dict[str, Any]:
    """Execute a GraphQL operation in-process and return JSON-safe payload data."""

    context = SimpleNamespace(user=user)
    execution_result = schema.execute(
        query,
        variable_values=variables,
        operation_name=operation_name,
        context_value=context,
    )

    payload: dict[str, Any] = {"data": execution_result.data}
    if execution_result.errors:
        payload["errors"] = [error.formatted for error in execution_result.errors]
    return payload


def graphql_query_tool(*, arguments: dict[str, Any], user) -> dict[str, Any]:
    """Run a GraphQL query/mutation using the authenticated MCP user context."""

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise McpToolError("The 'query' field must be a non-empty string.")

    variables = arguments.get("variables", {})
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise McpToolError("The 'variables' field must be an object when provided.")

    operation_name = arguments.get("operation_name")
    if operation_name is not None and not isinstance(operation_name, str):
        raise McpToolError("The 'operation_name' field must be a string when provided.")

    return _execute_graphql_query(
        query=query.strip(),
        variables=variables,
        operation_name=operation_name,
        user=user,
    )


def whoami_tool(*, user, **_kwargs) -> dict[str, Any]:
    """Return information about the MCP-authenticated user."""

    return {
        "id": user.pk,
        "username": user.get_username(),
        "is_staff": bool(user.is_staff),
        "is_superuser": bool(user.is_superuser),
        "groups": sorted(user.groups.values_list("name", flat=True)),
    }


def list_tools(*, allow: set[str] | None = None, deny: set[str] | None = None) -> dict[str, McpToolDefinition]:
    """Return registered MCP tool definitions filtered by optional allow/deny lists."""

    allow = allow or set()
    deny = deny or set()

    definitions = {
        "arthexis.graphql.query": McpToolDefinition(
            name="arthexis.graphql.query",
            description="Execute GraphQL queries and mutations using the authenticated user context.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "GraphQL document to execute.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "Optional GraphQL variables object.",
                        "default": {},
                    },
                    "operation_name": {
                        "type": "string",
                        "description": "Optional GraphQL operation name.",
                    },
                    "api_key": {
                        "type": "string",
                        "description": "MCP API key generated via manage.py create_mcp_api_key.",
                    },
                },
                "required": ["query", "api_key"],
                "additionalProperties": False,
            },
            handler=graphql_query_tool,
        ),
        "arthexis.auth.whoami": McpToolDefinition(
            name="arthexis.auth.whoami",
            description="Return profile details for the authenticated MCP API key owner.",
            input_schema={
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "description": "MCP API key generated via manage.py create_mcp_api_key.",
                    }
                },
                "required": ["api_key"],
                "additionalProperties": False,
            },
            handler=whoami_tool,
        ),
    }

    filtered: dict[str, McpToolDefinition] = {}
    for name, definition in sorted(definitions.items()):
        if allow and name not in allow:
            continue
        if name in deny:
            continue
        filtered[name] = definition
    return filtered


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Serialize a tool result dictionary to deterministic pretty JSON."""

    return json.dumps(result, indent=2, sort_keys=True)
