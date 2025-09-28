from __future__ import annotations

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, override_settings
from unittest import mock

import importlib


class MCPASGITests(SimpleTestCase):
    def tearDown(self) -> None:
        module = importlib.import_module("core.mcp.asgi")
        module.reset_cache()

    @override_settings(MCP_SIGIL_SERVER={"mount_path": "/agent"})
    def test_is_mcp_scope_matches_configured_prefix(self) -> None:
        module = importlib.import_module("core.mcp.asgi")
        module.reset_cache()
        self.assertTrue(module.is_mcp_scope({"path": "/agent/sse"}))
        self.assertTrue(module.is_mcp_scope({"path": "/agent"}))
        self.assertFalse(module.is_mcp_scope({"path": "/other"}))

    @override_settings(MCP_SIGIL_SERVER={"mount_path": "/agent"})
    def test_application_strips_mount_prefix_before_delegating(self) -> None:
        module = importlib.import_module("core.mcp.asgi")
        module.reset_cache()

        recorded: dict[str, str] = {}

        async def fake_app(scope, receive, send):
            recorded["path"] = scope["path"]
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        with mock.patch("core.mcp.asgi.SigilResolverServer") as server_cls:
            fastmcp = mock.Mock()
            fastmcp.sse_app.return_value = fake_app
            server_cls.return_value.build_fastmcp.return_value = fastmcp

            scope = {
                "type": "http",
                "path": "/agent/sse",
                "raw_path": b"/agent/sse",
                "method": "GET",
                "headers": [],
            }
            communicator = ApplicationCommunicator(module.application, scope)
            async_to_sync(communicator.send_input)({"type": "http.request"})
            response_start = async_to_sync(communicator.receive_output)()
            self.assertEqual(response_start["status"], 204)
            async_to_sync(communicator.receive_output)()
            async_to_sync(communicator.wait)()

        self.assertEqual(recorded["path"], "/sse")
