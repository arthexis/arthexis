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

    with pytest.raises(CommandError, match="requires credentials"):
        call_command("https", "--enable", "--godaddy", "example.com")


@pytest.mark.django_db
def test_https_godaddy_implies_enable(monkeypatch):
    """`https --godaddy` should implicitly behave like `https --enable --godaddy`."""

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

    def fake_provision(self, *, sudo: str = "sudo"):
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


@pytest.mark.django_db
def test_https_enable_direct_daphne_bypasses_nginx(monkeypatch):
    """Direct Daphne HTTPS mode should not apply nginx configuration."""

    from apps.certs.models import SelfSignedCertificate

    monkeypatch.setattr(
        SelfSignedCertificate,
        "generate",
        lambda self, **kwargs: "generated",
    )

    def fail_apply(self, *, reload: bool = True, remove: bool = False):
        raise AssertionError("nginx apply should not be called for daphne transport")

    monkeypatch.setattr(SiteConfiguration, "apply", fail_apply)

    out = StringIO()
    call_command(
        "https",
        "--enable",
        "--local",
        "--transport",
        "daphne",
        "--no-sudo",
        stdout=out,
    )

    config = SiteConfiguration.objects.get(name="localhost")
    assert config.transport == "daphne"
    assert config.protocol == "https"
    assert "HTTPS active via Daphne direct TLS, nginx bypassed." in out.getvalue()


@pytest.mark.django_db
def test_https_disable_flow_from_direct_mode(monkeypatch):
    """Disabling HTTPS should switch direct transport back to nginx."""

    config = SiteConfiguration.objects.create(
        name="localhost",
        protocol="https",
        transport="daphne",
        enabled=True,
    )

    def fake_apply(self, *, reload: bool = True, remove: bool = False):
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=True,
            message="nginx applied",
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--disable", "--local")
    config.refresh_from_db()
    assert config.protocol == "http"
    assert config.transport == "nginx"


@pytest.mark.django_db
def test_https_renew_due_certificates_still_works_for_direct_mode(monkeypatch):
    """Renew command should still renew due certs regardless of transport selection."""

    from django.utils import timezone
    from apps.certs.models import SelfSignedCertificate

    cert = SelfSignedCertificate.objects.create(
        name="local-https-localhost",
        domain="localhost",
        certificate_path="/tmp/fullchain.pem",
        certificate_key_path="/tmp/privkey.pem",
        expiration_date=timezone.now(),
    )
    SiteConfiguration.objects.create(
        name="localhost",
        protocol="https",
        transport="daphne",
        enabled=True,
        certificate=cert,
    )

    renewed = {"count": 0}

    def fake_renew(self, *, sudo: str = "sudo"):
        renewed["count"] += 1
        return "renewed"

    monkeypatch.setattr(SelfSignedCertificate, "renew", fake_renew)

    out = StringIO()
    call_command("https", "--renew", stdout=out)

    assert renewed["count"] == 1
    assert "Renewed 1 certificate(s)." in out.getvalue()


@pytest.mark.django_db
def test_https_report_identifies_direct_transport(monkeypatch):
    """Status report should identify direct TLS transport details."""

    from apps.certs.models import SelfSignedCertificate

    cert = SelfSignedCertificate.objects.create(
        name="direct-local",
        domain="localhost",
        certificate_path="/tmp/fullchain.pem",
        certificate_key_path="/tmp/privkey.pem",
    )
    SiteConfiguration.objects.create(
        name="localhost",
        protocol="https",
        transport="daphne",
        enabled=True,
        certificate=cert,
        tls_certificate_path=cert.certificate_path,
        tls_certificate_key_path=cert.certificate_key_path,
    )

    monkeypatch.setattr(
        SelfSignedCertificate,
        "verify",
        lambda self, *, sudo="sudo": services.CertificateVerificationResult(
            ok=True,
            messages=["certificate exists"],
        ),
    )

    out = StringIO()
    call_command("https", stdout=out)

    rendered = out.getvalue()
    assert "transport=daphne" in rendered
    assert "HTTPS active via Daphne direct TLS, nginx bypassed" in rendered
