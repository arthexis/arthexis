from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.sites.models import Site

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
