"""Compatibility tests for deprecated core MCP command shims."""

from __future__ import annotations

from importlib import import_module
from io import StringIO

import pytest
from django.core.management.base import OutputWrapper


@pytest.mark.parametrize(
    ("shim_path", "expected_canonical_path"),
    [
        (
            "apps.core.management.commands.create_mcp_api_key",
            "apps.mcp.management.commands.create_mcp_api_key",
        ),
        (
            "apps.core.management.commands.mcp_server",
            "apps.mcp.management.commands.mcp_server",
        ),
    ],
)
def test_compatibility_shim_is_importable(shim_path: str, expected_canonical_path: str) -> None:
    """Each compatibility entrypoint should remain importable."""

    shim_module = import_module(shim_path)

    assert shim_module.Command is not None
    assert shim_module.McpCommand is not None
    assert expected_canonical_path in shim_module.Command.help


@pytest.mark.parametrize(
    "shim_path",
    [
        "apps.core.management.commands.create_mcp_api_key",
        "apps.core.management.commands.mcp_server",
    ],
)
def test_shim_emits_deprecation_warning_and_delegates(monkeypatch, shim_path: str) -> None:
    """Each core shim should warn and delegate to the canonical command handle."""

    shim_module = import_module(shim_path)
    delegated = False

    def fake_handle(self, *args, **kwargs) -> None:
        """Capture delegation to the canonical command implementation."""

        nonlocal delegated
        delegated = True

    monkeypatch.setattr(shim_module.McpCommand, "handle", fake_handle)

    error_stream = StringIO()
    command = shim_module.Command()
    command.stderr = OutputWrapper(error_stream)

    command.handle()

    stderr_output = error_stream.getvalue()
    assert "Deprecation warning:" in stderr_output
    assert shim_path in stderr_output
    assert delegated is True
