"""Compatibility import test module for moved MCP tests."""


def test_compat_imports_mcp_server() -> None:
    """Verify the compatibility MCP server command entrypoint is importable."""

    from apps.core.management.commands.mcp_server import Command

    assert Command is not None
