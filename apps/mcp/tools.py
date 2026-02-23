"""MCP tool registry for Arthexis non-CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from graphql import parse
from graphql.language import FragmentSpreadNode, InlineFragmentNode, OperationDefinitionNode

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


MAX_GRAPHQL_QUERY_LENGTH = 20_000
MAX_GRAPHQL_DEPTH = 12


def _max_selection_depth(selection_set, fragment_depths: dict[str, int]) -> int:
    if selection_set is None:
        return 0

    max_depth = 0
    for selection in selection_set.selections:
        nested_depth = 0

        if isinstance(selection, FragmentSpreadNode):
            nested_depth = fragment_depths.get(selection.name.value, 0)
        elif isinstance(selection, InlineFragmentNode):
            nested_depth = _max_selection_depth(selection.selection_set, fragment_depths)
        elif getattr(selection, "selection_set", None) is not None:
            nested_depth = _max_selection_depth(selection.selection_set, fragment_depths)

        max_depth = max(max_depth, 1 + nested_depth)

    return max_depth


def _max_graphql_depth(query: str) -> int:
    document = parse(query)

    fragment_definitions = {
        definition.name.value: definition
        for definition in document.definitions
        if definition.kind == "fragment_definition"
    }

    fragment_depths = {name: 0 for name in fragment_definitions}
    for _ in range(len(fragment_definitions)):
        changed = False
        for name, definition in fragment_definitions.items():
            depth = _max_selection_depth(definition.selection_set, fragment_depths)
            if depth != fragment_depths[name]:
                fragment_depths[name] = depth
                changed = True
        if not changed:
            break

    operation_depths = [
        _max_selection_depth(definition.selection_set, fragment_depths)
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
    ]
    return max(operation_depths, default=0)


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
        raise McpToolError("The 'query' field must be a non-empty string when provided.")

    query = query.strip()
    if len(query) > MAX_GRAPHQL_QUERY_LENGTH:
        raise McpToolError("The GraphQL query exceeds the maximum allowed length.")

    try:
        query_depth = _max_graphql_depth(query)
    except Exception as exc:  # GraphQL parser exceptions are surfaced as tool input errors.
        raise McpToolError(f"Invalid GraphQL query: {exc}") from exc

    if query_depth > MAX_GRAPHQL_DEPTH:
        raise McpToolError(
            f"The GraphQL query depth exceeds the limit of {MAX_GRAPHQL_DEPTH}."
        )

    variables = arguments.get("variables", {})
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise McpToolError("The 'variables' field must be an object when provided.")

    operation_name = arguments.get("operation_name")
    if operation_name is not None and not isinstance(operation_name, str):
        raise McpToolError("The 'operation_name' field must be a string when provided.")

    return _execute_graphql_query(
        query=query,
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
