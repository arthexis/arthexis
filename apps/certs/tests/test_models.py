import pytest
from django.utils import timezone

from apps.certs import services
from apps.certs.models import SelfSignedCertificate


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

    monkeypatch.setattr(
        services,
        "generate_self_signed_certificate",
        fake_generate_self_signed_certificate,
    )
    expiration = later + timezone.timedelta(days=30)
    monkeypatch.setattr(
        services,
        "get_certificate_expiration",
        lambda **kwargs: expiration,
    )

    message = certificate.generate(sudo="")

    certificate.refresh_from_db()
    assert message == "generated"
    assert certificate.expiration_date == expiration
    assert certificate.last_generated_at == later
    assert captured["domain"] == "demo.example.com"
    assert captured["days_valid"] == 30
    assert captured["key_length"] == 1024


@pytest.mark.django_db
def test_certificate_provision_dispatches_to_self_signed(monkeypatch):
    certificate = SelfSignedCertificate.objects.create(
        name="dispatch-self-signed",
        domain="dispatch-self.example.com",
        certificate_path="/tmp/dispatch/fullchain.pem",
        certificate_key_path="/tmp/dispatch/privkey.pem",
    )
    monkeypatch.setattr(certificate, "generate", lambda *, sudo="sudo": "generated")

    assert certificate.provision(sudo="") == "generated"


@pytest.mark.django_db
def test_certificate_renew_regenerates_application_certificate(monkeypatch):
    certificate = SelfSignedCertificate.objects.create(
        name="renew-self-signed",
        domain="renew.example.com",
        certificate_path="/tmp/renew/fullchain.pem",
        certificate_key_path="/tmp/renew/privkey.pem",
        expiration_date=timezone.now() - timezone.timedelta(minutes=1),
    )
    monkeypatch.setattr(certificate, "provision", lambda *, sudo="sudo": "renewed")
    monkeypatch.setattr(
        certificate, "update_expiration_date", lambda *, sudo="sudo": None
    )

    assert certificate.renew(sudo="") == "renewed"
