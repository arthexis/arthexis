from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.mcp.models import MCPServer


class MCPManifestTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="agent", email="agent@example.com", password="secret"
        )
        self.server = MCPServer.objects.create(
            name="Default MCP",
            acting_user=self.user,
            is_enabled=True,
        )
        self.client = Client()

    def test_manifest_requires_secret(self):
        url = reverse("mcp_api:mcp_api_manifest", args=[self.server.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_manifest_includes_endpoints(self):
        url = reverse("mcp_api:mcp_api_manifest", args=[self.server.slug])
        response = self.client.get(url, {"secret": self.server.api_secret})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["acting_user"], self.user.username)
        self.assertTrue(payload["enabled"])
        self.assertIn("rpc", payload["endpoints"])
        self.assertIn("events", payload["endpoints"])
        self.assertEqual(
            payload["endpoints"]["manifest"],
            response.wsgi_request.build_absolute_uri(url),
        )

    def test_disabled_server_is_hidden(self):
        self.server.is_enabled = False
        self.server.save(update_fields=["is_enabled"])

        url = reverse("mcp_api:mcp_api_manifest", args=[self.server.slug])
        response = self.client.get(url, {"secret": self.server.api_secret})
        self.assertEqual(response.status_code, 404)

    def test_rotate_secret_requires_staff(self):
        url = reverse("mcp_api:mcp_api_rotate_secret", args=[self.server.slug])
        old_secret = self.server.api_secret

        rotate_response = self.client.post(url)
        self.assertEqual(rotate_response.status_code, 302)

        admin = get_user_model().objects.create_user(
            username="admin", email="admin@example.com", password="secret", is_staff=True
        )
        self.client.force_login(admin)

        rotate_response = self.client.post(url)
        self.assertEqual(rotate_response.status_code, 200)
        new_secret = rotate_response.json()["secret"]
        self.server.refresh_from_db()
        self.assertNotEqual(new_secret, old_secret)
