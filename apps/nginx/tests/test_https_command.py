"""Tests for the nginx https management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.certs.models import CertbotCertificate
from apps.nginx import services
from apps.nginx.models import SiteConfiguration


@pytest.mark.django_db
def test_https_enable_with_godaddy_sets_dns_challenge(monkeypatch):
    """`https --enable --godaddy` should configure a certbot DNS challenge certificate."""

    provision_calls: dict[str, str] = {}

    def fake_provision(self, *, sudo: str = "sudo"):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    from apps.nginx.management.commands import https as https_module

    monkeypatch.setattr(
        https_module.Command, "_validate_godaddy_setup", lambda self, certificate: None
    )

    call_command("https", "--enable", "--godaddy", "example.com", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.com")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.GODADDY
    assert provision_calls["sudo"] == ""


@pytest.mark.django_db
def test_https_enable_with_godaddy_requires_dns_credential(monkeypatch):
    """GoDaddy mode should stop with guidance if no credential is configured."""

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    with pytest.raises(CommandError, match="requires an enabled DNS credential"):
        call_command("https", "--enable", "--godaddy", "example.com")


@pytest.mark.django_db
def test_https_enable_with_godaddy_reports_manual_steps(monkeypatch):
    """GoDaddy mode should print setup guidance for administrators."""

    from apps.dns.models import DNSProviderCredential

    credential = DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
    )

    def fake_provision(self, *, sudo: str = "sudo"):
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command("https", "--enable", "--godaddy", "example.org", stdout=out)

    cert = SiteConfiguration.objects.get(
        name="example.org"
    ).certificate._specific_certificate
    assert cert.dns_credential_id == credential.id
    rendered = out.getvalue()
    assert "Ensure certbot and Python requests are available" in rendered
