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

    def fake_provision(self, *, sudo: str = "sudo", dns_use_sandbox=None):
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

    with pytest.raises(CommandError, match="requires credentials"):
        call_command("https", "--enable", "--godaddy", "example.com")


@pytest.mark.django_db
def test_https_godaddy_implies_enable(monkeypatch):
    """`https --godaddy` should implicitly behave like `https --enable --godaddy`."""

    provision_calls: dict[str, str] = {}

    def fake_provision(self, *, sudo: str = "sudo", dns_use_sandbox=None):
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

    call_command("https", "--godaddy", "example.net", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.net")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.GODADDY
    assert provision_calls["sudo"] == ""


@pytest.mark.django_db
def test_https_certbot_implies_enable(monkeypatch):
    """`https --certbot` should implicitly behave like `https --enable --certbot`."""

    provision_calls: dict[str, str] = {}

    def fake_provision(self, *, sudo: str = "sudo", dns_use_sandbox=None):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_provision)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--certbot", "example.net", "--no-sudo")

    config = SiteConfiguration.objects.get(name="example.net")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.NGINX
    assert provision_calls["sudo"] == ""




@pytest.mark.django_db
def test_https_site_url_implies_enable_and_creates_managed_site(monkeypatch):
    """`https --site wss://...` should normalize host and stage managed site metadata."""

    provision_calls: dict[str, str] = {}

    def fake_request(self, *, sudo: str = "sudo", dns_use_sandbox=None):
        provision_calls["sudo"] = sudo
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(changed=True, validated=True, reloaded=True, message="ok")

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--site", "wss://porsche-abb-1.gelectriic.com/", "--no-sudo")

    config = SiteConfiguration.objects.get(name="porsche-abb-1.gelectriic.com")
    cert = config.certificate._specific_certificate
    assert isinstance(cert, CertbotCertificate)
    assert cert.challenge_type == CertbotCertificate.ChallengeType.NGINX
    assert provision_calls["sudo"] == ""

    from django.contrib.sites.models import Site

    site = Site.objects.get(domain="porsche-abb-1.gelectriic.com")
    assert getattr(site, "managed", False) is True
    assert getattr(site, "require_https", False) is True


@pytest.mark.django_db
def test_https_site_rejects_local_combination():
    """`--site` and `--local` should fail to avoid contradictory certificate intent."""

    with pytest.raises(CommandError, match="cannot be combined"):
        call_command("https", "--enable", "--local", "--site", "example.com")
@pytest.mark.django_db
def test_prompt_for_godaddy_credential_allows_redirected_stdout(monkeypatch):
    """Credential prompts should still run when stdout is redirected but stdin is interactive."""

    import sys
    from apps.dns.models import DNSProviderCredential
    from apps.nginx.management.commands.https import Command

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    prompt_map = {
        "Enter credentials now and save to DNS Credentials? [y/N]: ": "y",
        "GoDaddy API key: ": "key-123",
        "GoDaddy customer ID (optional): ": "customer-42",
        "Use GoDaddy OTE sandbox environment? [y/N]: ": "n",
    }
    monkeypatch.setattr("builtins.input", lambda prompt="": prompt_map[prompt])
    monkeypatch.setattr("apps.nginx.management.commands.https.getpass", lambda _prompt='': "secret-456")

    command = Command()
    credential = command._prompt_for_godaddy_credential("example.edu")

    assert credential is not None
    assert credential.provider == DNSProviderCredential.Provider.GODADDY
    assert credential.api_key == "key-123"
    assert credential.api_secret == "secret-456"
    assert credential.customer_id == "customer-42"
    assert credential.default_domain == "example.edu"
    assert credential.is_enabled is True
    assert credential.use_sandbox is False


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

    def fake_provision(self, *, sudo: str = "sudo", dns_use_sandbox=None):
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


@pytest.mark.django_db
def test_https_enable_with_godaddy_sandbox_override(monkeypatch):
    """`https --godaddy --sandbox` should override credential sandbox mode per run."""

    from apps.dns.models import DNSProviderCredential

    credential = DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
        use_sandbox=False,
    )

    provision_calls: dict[str, object] = {}

    def fake_request(self, *, sudo: str = "sudo", dns_use_sandbox=None):
        provision_calls["sudo"] = sudo
        provision_calls["dns_use_sandbox"] = dns_use_sandbox
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    out = StringIO()
    call_command(
        "https",
        "--enable",
        "--godaddy",
        "example.org",
        "--sandbox",
        stdout=out,
    )

    cert = SiteConfiguration.objects.get(
        name="example.org"
    ).certificate._specific_certificate
    assert cert.dns_credential_id == credential.id
    assert provision_calls["dns_use_sandbox"] is True


@pytest.mark.django_db
def test_https_enable_with_godaddy_no_sandbox_override(monkeypatch):
    """`https --godaddy --no-sandbox` should force production DNS API for this run."""

    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="api-key",
        api_secret="api-secret",
        is_enabled=True,
        use_sandbox=True,
    )

    provision_calls: dict[str, object] = {}

    def fake_request(self, *, sudo: str = "sudo", dns_use_sandbox=None):
        provision_calls["dns_use_sandbox"] = dns_use_sandbox
        return "requested"

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True, validated=True, reloaded=True, message="ok"
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--godaddy", "example.net", "--no-sandbox")

    assert provision_calls["dns_use_sandbox"] is False
