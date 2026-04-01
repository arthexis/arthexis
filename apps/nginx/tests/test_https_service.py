from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.nginx.management.commands.https_parts.service import HttpsProvisioningService


def _service() -> HttpsProvisioningService:
    command = SimpleNamespace(
        stdout=SimpleNamespace(write=lambda _message: None),
        style=SimpleNamespace(SUCCESS=lambda message: message),
    )
    return HttpsProvisioningService(command=command)


def _base_options() -> dict[str, object]:
    return {
        "enable": False,
        "disable": False,
        "renew": False,
        "validate": False,
        "certbot": None,
        "godaddy": None,
        "site": None,
        "domain": None,
        "migrate_from": None,
        "local": False,
        "sandbox": False,
        "no_sandbox": False,
        "no_reload": True,
        "no_sudo": True,
        "force_renewal": False,
        "key": None,
        "static_ip": None,
        "warn_days": 14,
    }


@pytest.mark.django_db
def test_handle_rejects_godaddy_automation_flag():
    service = _service()
    options = _base_options()
    options["godaddy"] = "csms.example.com"
    with pytest.raises(CommandError, match="Automated GoDaddy DNS setup was removed"):
        service.handle(options)


@pytest.mark.django_db
def test_handle_rejects_godaddy_auxiliary_flags():
    service = _service()
    options = _base_options()
    options["key"] = "legacy-credential"
    with pytest.raises(CommandError, match="no longer supported"):
        service.handle(options)
