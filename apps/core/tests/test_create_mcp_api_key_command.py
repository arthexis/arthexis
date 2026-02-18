"""Compatibility import test module for moved MCP command tests."""


def test_compatibility_import() -> None:
    """Verify the compatibility command entrypoint remains importable."""

    from apps.core.management.commands.create_mcp_api_key import Command

    assert Command is not None
