"""Compatibility tests for the deprecated core MCP API key command shim."""

from __future__ import annotations

from io import StringIO

from django.core.management.base import OutputWrapper


def test_compatibility_import() -> None:
    """Verify the compatibility command entrypoint remains importable."""

    from apps.core.management.commands.create_mcp_api_key import Command

    assert Command is not None


def test_create_key_shim_emits_deprecation_warning(monkeypatch) -> None:
    """The core shim should warn and delegate to the canonical command handle."""

    from apps.core.management.commands.create_mcp_api_key import Command, McpCommand

    delegated = False

    def fake_handle(self, *args, **kwargs) -> None:
        """Capture delegation to the canonical command implementation."""

        nonlocal delegated
        delegated = True

    monkeypatch.setattr(McpCommand, "handle", fake_handle)

    error_stream = StringIO()
    command = Command()
    command.stderr = OutputWrapper(error_stream)

    command.handle()

    stderr_output = error_stream.getvalue()
    assert "Deprecation warning:" in stderr_output
    assert "apps.core.management.commands.create_mcp_api_key" in stderr_output
    assert delegated is True
