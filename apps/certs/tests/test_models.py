import pytest
from django.utils import timezone

from apps.certs import services
from apps.certs.models import CertbotCertificate, SelfSignedCertificate


@pytest.mark.django_db
def test_certbot_certificate_request_updates_state(monkeypatch):
    certificate = CertbotCertificate.objects.create(
        name="certbot", domain="example.com", certificate_path="", certificate_key_path=""
    )

    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)

    captured = {}

    def fake_request_certbot_certificate(**kwargs):
        captured.update(kwargs)
        return "requested"

    monkeypatch.setattr(services, "request_certbot_certificate", fake_request_certbot_certificate)

    message = certificate.request(sudo="")

    certificate.refresh_from_db()
    assert message == "requested"
    assert certificate.last_requested_at == now
    assert certificate.certificate_path.endswith("fullchain.pem")
    assert certificate.certificate_key_path.endswith("privkey.pem")
    assert captured["domain"] == "example.com"
    assert captured["certificate_path"].name == "fullchain.pem"


@pytest.mark.django_db
def test_self_signed_certificate_generate_updates_state(monkeypatch):
    certificate = SelfSignedCertificate.objects.create(
        name="self-signed",
        domain="demo.example.com",
        certificate_path="/tmp/demo/fullchain.pem",
        certificate_key_path="/tmp/demo/privkey.pem",
        valid_days=30,
        key_length=1024,
    )

    later = timezone.now() + timezone.timedelta(minutes=5)
    monkeypatch.setattr(timezone, "now", lambda: later)

    captured = {}

    def fake_generate_self_signed_certificate(**kwargs):
        captured.update(kwargs)
        return "generated"

    monkeypatch.setattr(services, "generate_self_signed_certificate", fake_generate_self_signed_certificate)

    message = certificate.generate(sudo="")

    certificate.refresh_from_db()
    assert message == "generated"
    assert certificate.last_generated_at == later
    assert captured["domain"] == "demo.example.com"
    assert captured["days_valid"] == 30
    assert captured["key_length"] == 1024


@pytest.mark.django_db
def test_certificate_provision_dispatches(monkeypatch):
    certbot = CertbotCertificate.objects.create(
        name="dispatch-certbot", domain="dispatch.example.com", certificate_path="", certificate_key_path=""
    )
    self_signed = SelfSignedCertificate.objects.create(
        name="dispatch-self-signed",
        domain="dispatch-self.example.com",
        certificate_path="/tmp/dispatch/fullchain.pem",
        certificate_key_path="/tmp/dispatch/privkey.pem",
    )

    monkeypatch.setattr(certbot, "request", lambda *, sudo="sudo": "requested")
    monkeypatch.setattr(self_signed, "generate", lambda *, sudo="sudo": "generated")

    assert certbot.provision(sudo="") == "requested"
    assert self_signed.provision(sudo="") == "generated"
