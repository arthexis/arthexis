import io

import pytest
from django.core.management import call_command

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.certs.services import CertificateVerificationResult
from apps.nginx import services
from apps.nginx.models import SiteConfiguration


@pytest.mark.django_db
def test_https_command_enable_local_creates_local_config(monkeypatch):
    captured = {}

    def fake_generate(self, *, sudo="sudo", subject_alt_names=None):
        captured["sudo"] = sudo
        captured["subject_alt_names"] = subject_alt_names
        return "generated"

    def fake_apply(self, reload=True, remove=False):
        captured["reload"] = reload
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=True,
            message="applied",
        )

    monkeypatch.setattr(SelfSignedCertificate, "generate", fake_generate)
    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--enable", "--no-reload", "--no-sudo")

    config = SiteConfiguration.objects.get(name="localhost")
    assert config.protocol == "https"
    assert config.enabled is True
    assert config.certificate is not None
    assert isinstance(config.certificate._specific_certificate, SelfSignedCertificate)
    assert config.certificate.domain == "localhost"
    assert captured["subject_alt_names"] == ["localhost", "127.0.0.1", "::1"]
    assert captured["sudo"] == ""
    assert captured["reload"] is False


@pytest.mark.django_db
def test_https_command_disable_preserves_certificate(monkeypatch):
    certificate = SelfSignedCertificate.objects.create(
        name="local-https-localhost",
        domain="localhost",
        certificate_path="/tmp/localhost.pem",
        certificate_key_path="/tmp/localhost.key",
    )
    config = SiteConfiguration.objects.create(
        name="localhost",
        protocol="https",
        certificate=certificate,
    )
    captured = {}

    def fake_apply(self, reload=True, remove=False):
        captured["reload"] = reload
        return services.ApplyResult(
            changed=True,
            validated=True,
            reloaded=True,
            message="disabled",
        )

    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command("https", "--disable", "--no-reload")

    config.refresh_from_db()
    assert config.protocol == "http"
    assert config.certificate_id == certificate.id
    assert captured["reload"] is False


@pytest.mark.django_db
def test_https_command_report_outputs_certificate_status(monkeypatch):
    certificate = SelfSignedCertificate.objects.create(
        name="local-https-localhost",
        domain="localhost",
        certificate_path="/tmp/localhost.pem",
        certificate_key_path="/tmp/localhost.key",
    )
    SiteConfiguration.objects.create(
        name="localhost",
        protocol="https",
        certificate=certificate,
    )

    def fake_verify(self, *, sudo="sudo"):
        return CertificateVerificationResult(ok=True, messages=["ok"])

    monkeypatch.setattr(CertificateBase, "verify", fake_verify)

    output = io.StringIO()
    call_command("https", stdout=output)

    text = output.getvalue()
    assert "HTTPS status report" in text
    assert "Certificate status: valid." in text


@pytest.mark.django_db
def test_https_command_enable_certbot_with_godaddy_uses_dns_credentials(monkeypatch):
    from apps.dns.models import DNSProviderCredential

    DNSProviderCredential.objects.create(
        provider=DNSProviderCredential.Provider.GODADDY,
        api_key="godaddy-key",
        api_secret="godaddy-secret",
        default_domain="example.com",
        is_enabled=True,
    )

    captured = {}

    def fake_request(
        self,
        *,
        sudo="sudo",
        validation_provider=None,
        dns_api_key=None,
        dns_api_secret=None,
        dns_propagation_seconds=60,
    ):
        captured["sudo"] = sudo
        captured["validation_provider"] = validation_provider
        captured["dns_api_key"] = dns_api_key
        captured["dns_api_secret"] = dns_api_secret
        captured["dns_propagation_seconds"] = dns_propagation_seconds
        return "requested"

    def fake_apply(self, reload=True, remove=False):
        return services.ApplyResult(changed=True, validated=True, reloaded=True, message="applied")

    monkeypatch.setattr(CertbotCertificate, "request", fake_request)
    monkeypatch.setattr(SiteConfiguration, "apply", fake_apply)

    call_command(
        "https",
        "--enable",
        "--certbot",
        "foo.example.com",
        "--godaddy",
        "--dns-propagation-seconds",
        "90",
        "--no-sudo",
    )

    assert captured["sudo"] == ""
    assert captured["validation_provider"] == "godaddy"
    assert captured["dns_api_key"] == "godaddy-key"
    assert captured["dns_api_secret"] == "godaddy-secret"
    assert captured["dns_propagation_seconds"] == 90


@pytest.mark.django_db
def test_https_command_godaddy_requires_certbot_domain():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError) as exc:
        call_command("https", "--enable", "--godaddy")

    assert "--godaddy requires --certbot DOMAIN" in str(exc.value)
