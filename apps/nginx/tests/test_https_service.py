from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.sites.models import Site
from django.test import override_settings

from apps.nginx.management.commands.https_parts.service import HttpsProvisioningService


@pytest.mark.django_db
def test_ensure_managed_site_sets_instance_default_site(monkeypatch):
    command = SimpleNamespace(
        stdout=SimpleNamespace(write=lambda _message: None),
        style=SimpleNamespace(SUCCESS=lambda message: message),
    )
    service = HttpsProvisioningService(command=command)
    monkeypatch.setattr(
        "apps.nginx.management.commands.https_parts.service.update_local_nginx_scripts",
        lambda: None,
    )

    Site.objects.filter(pk=42).delete()

    with override_settings(SITE_ID=42):
        service._ensure_managed_site("arthexis.com", require_https=True)

    created = Site.objects.get(pk=42)
    assert created.domain == "arthexis.com"
    assert created.name == "arthexis.com"
    assert created.managed is True
    assert created.require_https is True
