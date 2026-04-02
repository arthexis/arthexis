from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.sites.models import Site

from apps.links.models import Reference
from apps.meta.models import WhatsAppChatBridge
from apps.nginx.management.commands.https_parts.service import HttpsProvisioningService
from apps.nodes.models import Node

pytestmark = pytest.mark.django_db


def _build_service():
    command = SimpleNamespace(
        stdout=SimpleNamespace(write=lambda _message: None),
        style=SimpleNamespace(SUCCESS=lambda message: message),
    )
    return HttpsProvisioningService(command=command)


def test_ensure_managed_site_sets_requested_domain_as_default_site(settings):
    default_site = Site.objects.get(pk=settings.SITE_ID)
    default_site.domain = "old.example.com"
    default_site.name = "old.example.com"
    default_site.save(update_fields=["domain", "name"])

    target_site = Site.objects.create(
        domain="porsche.gelectriic.com",
        name="porsche.gelectriic.com",
    )
    node = Node.objects.create(hostname="node-a", base_site=target_site)

    service = _build_service()
    service._ensure_managed_site("porsche.gelectriic.com", require_https=True)

    default_site.refresh_from_db()
    node.refresh_from_db()

    assert default_site.domain == "porsche.gelectriic.com"
    assert default_site.name == "porsche.gelectriic.com"
    assert default_site.managed is True
    assert default_site.require_https is True
    assert node.base_site_id == default_site.id
    assert not Site.objects.filter(pk=target_site.pk).exists()


def test_ensure_managed_site_preserves_source_site_configuration(settings):
    default_site = Site.objects.get(pk=settings.SITE_ID)
    default_site.enable_public_chat = False
    default_site.save(update_fields=["enable_public_chat"])

    target_site = Site.objects.create(
        domain="porsche.gelectriic.com",
        name="porsche.gelectriic.com",
        enable_public_chat=True,
    )

    service = _build_service()
    service._ensure_managed_site("porsche.gelectriic.com", require_https=True)

    default_site.refresh_from_db()

    assert default_site.enable_public_chat is True
    assert not Site.objects.filter(pk=target_site.pk).exists()


def test_ensure_managed_site_reassigns_many_to_many_relations(settings):
    default_site = Site.objects.get(pk=settings.SITE_ID)
    target_site = Site.objects.create(
        domain="porsche.gelectriic.com",
        name="porsche.gelectriic.com",
    )
    reference = Reference.objects.create(alt_text="Spec Sheet", value="https://example.test")
    reference.sites.add(target_site)

    service = _build_service()
    service._ensure_managed_site("porsche.gelectriic.com", require_https=True)

    reference.refresh_from_db()

    assert reference.sites.filter(pk=default_site.pk).exists()
    assert reference.sites.filter(pk=target_site.pk).count() == 0


def test_ensure_managed_site_handles_unique_fk_collisions(settings):
    default_site = Site.objects.get(pk=settings.SITE_ID)
    target_site = Site.objects.create(
        domain="porsche.gelectriic.com",
        name="porsche.gelectriic.com",
    )
    default_bridge = WhatsAppChatBridge.objects.create(
        site=default_site,
        phone_number_id="12345",
        access_token="token-default",
        is_default=False,
    )
    source_bridge = WhatsAppChatBridge.objects.create(
        site=target_site,
        phone_number_id="54321",
        access_token="token-source",
        is_default=False,
    )

    service = _build_service()
    service._ensure_managed_site("porsche.gelectriic.com", require_https=True)

    default_bridge.refresh_from_db()

    assert default_bridge.site_id == default_site.pk
    assert not WhatsAppChatBridge.objects.filter(pk=source_bridge.pk).exists()
    assert not Site.objects.filter(pk=target_site.pk).exists()
