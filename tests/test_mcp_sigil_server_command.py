from __future__ import annotations

from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings


class MCPSigilServerCommandTests(TestCase):
    @override_settings(
        MCP_SIGIL_SERVER={
            "host": "127.0.0.1",
            "port": 8123,
            "api_keys": ["alpha", "beta"],
            "required_scopes": ["sigils:read"],
            "issuer_url": None,
            "resource_server_url": None,
        }
    )
    def test_uses_configured_port_when_option_missing(self) -> None:
        with mock.patch(
            "core.management.commands.mcp_sigil_server.SigilResolverServer"
        ) as mock_server, mock.patch(
            "core.management.commands.mcp_sigil_server.asyncio.run"
        ) as mock_async_run:
            fastmcp_mock = mock.Mock()
            mock_server.return_value.build_fastmcp.return_value = fastmcp_mock

            call_command("mcp_sigil_server", "--no-auth")

        config = mock_server.call_args[0][0]
        self.assertEqual(config["port"], 8123)
        self.assertEqual(config["api_keys"], [])
        self.assertEqual(config["host"], "127.0.0.1")
        mock_async_run.assert_called_once()

    def test_invalid_port_argument_raises_error(self) -> None:
        with mock.patch(
            "core.management.commands.mcp_sigil_server.SigilResolverServer"
        ), mock.patch("core.management.commands.mcp_sigil_server.asyncio.run"):
            with self.assertRaises(CommandError):
                call_command("mcp_sigil_server", "--port", "not-a-number")
