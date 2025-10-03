from __future__ import annotations

import asyncio
import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from django.contrib.sites.models import Site

from core.mcp.schemas import ResolveOptions
from core.mcp.server import SigilResolverServer, resolve_base_urls
from core.mcp.service import (
    ResolutionResult,
    SigilResolverService,
    SigilRootCatalog,
    SigilSessionState,
)
from core.models import OdooProfile, SigilRoot


class SigilResolverServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create(
            username="resolver", email="resolver@example.com"
        )
        cls.profile = OdooProfile.objects.create(
            user=cls.user,
            host="h",
            database="db",
            username="odoo",
            password="secret",
        )
        ct = ContentType.objects.get_for_model(OdooProfile)
        SigilRoot.objects.update_or_create(
            prefix="ODOO",
            defaults={
                "context_type": SigilRoot.Context.ENTITY,
                "content_type": ct,
            },
        )

    def test_resolve_text_uses_session_context(self) -> None:
        catalog = SigilRootCatalog()
        catalog.refresh()
        service = SigilResolverService(catalog)
        state = SigilSessionState()
        state.context[OdooProfile] = str(self.profile.pk)
        result = service.resolve_text(
            "user=[ODOO.USERNAME]", session_context=state.context
        )
        self.assertIsInstance(result, ResolutionResult)
        self.assertEqual(result.resolved, "user=odoo")
        self.assertEqual(result.unresolved, [])

    def test_skip_unknown_tokens(self) -> None:
        service = SigilResolverService(SigilRootCatalog())
        result = service.resolve_text(
            "value=[UNKNOWN.THING]",
            options=ResolveOptions(skipUnknown=True),
        )
        self.assertEqual(result.resolved, "value=")
        self.assertEqual(result.unresolved, ["UNKNOWN.THING"])

    def test_update_session_context_clears_entries(self) -> None:
        service = SigilResolverService(SigilRootCatalog())
        state = SigilSessionState()
        stored = service.update_session_context(
            state, {"core.OdooProfile": self.profile.pk}
        )
        self.assertEqual(stored, ["core.OdooProfile"])
        stored = service.update_session_context(state, {"core.OdooProfile": None})
        self.assertEqual(stored, [])


class SigilResolverServerTests(TestCase):
    def setUp(self) -> None:
        self.server = SigilResolverServer(
            {"api_keys": [], "host": "127.0.0.1", "port": 0}
        )

    def test_catalog_updates_via_signals(self) -> None:
        ct = ContentType.objects.get_for_model(OdooProfile)
        prefix = "MCPTEST"
        SigilRoot.objects.create(
            prefix=prefix, context_type=SigilRoot.Context.ENTITY, content_type=ct
        )
        description = self.server.catalog.describe(prefix)
        self.assertEqual(description.prefix, prefix)
        SigilRoot.objects.filter(prefix=prefix).delete()
        with self.assertRaises(ValueError):
            self.server.catalog.describe(prefix)

    def test_build_fastmcp_respects_authentication_toggle(self) -> None:
        secured = SigilResolverServer(
            {"api_keys": ["secret"], "host": "127.0.0.1", "port": 9000}
        )
        secured_server = secured.build_fastmcp()
        self.assertIsNotNone(secured_server._token_verifier)
        self.assertIsNotNone(secured_server.settings.auth)

        open_server = SigilResolverServer(
            {"api_keys": [], "host": "127.0.0.1", "port": 9000}
        )
        open_fastmcp = open_server.build_fastmcp()
        self.assertIsNone(open_fastmcp._token_verifier)
        self.assertIsNone(open_fastmcp.settings.auth)

    def test_build_fastmcp_uses_configured_mount_path(self) -> None:
        server = SigilResolverServer(
            {"api_keys": [], "host": "127.0.0.1", "port": 9000, "mount_path": "/bridge"}
        )
        fastmcp = server.build_fastmcp()
        self.assertEqual(fastmcp.settings.mount_path, "/bridge")

    def test_resource_lists_roots(self) -> None:
        fastmcp = self.server.build_fastmcp()
        resource = asyncio.run(
            fastmcp._resource_manager.get_resource("resource://sigils/roots")
        )
        payload = self.server.service.list_roots()
        rendered = json.loads(asyncio.run(resource.read()))
        expected = sorted(
            (entry.model_dump(by_alias=True) for entry in payload),
            key=lambda item: item["prefix"],
        )
        self.assertEqual(
            expected, sorted(rendered["roots"], key=lambda item: item["prefix"])
        )


class SigilResolverServerURLTests(TestCase):
    def setUp(self) -> None:
        self.site = Site.objects.get_current()
        self.original_domain = self.site.domain
        self.original_name = self.site.name
        self.addCleanup(self._restore_site)

    def _restore_site(self) -> None:
        self.site.domain = self.original_domain
        self.site.name = self.original_name
        self.site.save()

    def test_resolve_base_urls_uses_site_domain(self) -> None:
        self.site.domain = "mcp.example.test"
        self.site.save()
        config = {"host": "0.0.0.0", "port": 8800, "api_keys": ["secret"], "required_scopes": []}
        base_url, issuer_url = resolve_base_urls(config)
        self.assertEqual(base_url, "https://mcp.example.test")
        self.assertEqual(issuer_url, base_url)

    def test_resolve_base_urls_falls_back_to_host(self) -> None:
        self.site.domain = ""
        self.site.save()
        config = {"host": "127.0.0.1", "port": 8800, "api_keys": ["secret"], "required_scopes": []}
        base_url, issuer_url = resolve_base_urls(config)
        self.assertEqual(base_url, "http://127.0.0.1:8800")
        self.assertEqual(issuer_url, base_url)

    def test_resolve_base_urls_honors_full_url_site_domain(self) -> None:
        self.site.domain = "https://resolver.example.com"
        self.site.save()
        config = {"host": "0.0.0.0", "port": 8800, "api_keys": ["secret"], "required_scopes": []}
        base_url, issuer_url = resolve_base_urls(config)
        self.assertEqual(base_url, "https://resolver.example.com")
        self.assertEqual(issuer_url, base_url)

    def test_resolve_base_urls_appends_mount_path_when_derived(self) -> None:
        self.site.domain = "resolver.example.com"
        self.site.save()
        config = {
            "host": "0.0.0.0",
            "port": 8800,
            "api_keys": ["secret"],
            "required_scopes": [],
            "mount_path": "/mcp",
        }
        base_url, issuer_url = resolve_base_urls(config)
        self.assertEqual(base_url, "https://resolver.example.com/mcp")
        self.assertEqual(issuer_url, base_url)
