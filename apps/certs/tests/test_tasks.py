import pytest

from django.utils import timezone

from apps.certs.tasks import refresh_certificate_expirations
from apps.certs.models import SelfSignedCertificate

pytestmark = pytest.mark.critical


@pytest.mark.django_db
def test_refresh_certificate_expirations_rechecks_before_renewal(monkeypatch):
    """Task skips renewal when a second expiration check shows cert is no longer due."""
    now = timezone.now()
    certificate = SelfSignedCertificate.objects.create(
        name="recheck-skip",
        domain="skip.example.com",
        certificate_path="/tmp/skip/fullchain.pem",
        certificate_key_path="/tmp/skip/privkey.pem",
        expiration_date=now - timezone.timedelta(minutes=1),
        auto_renew=True,
    )

    monkeypatch.setattr(timezone, "now", lambda: now)

    calls = {"count": 0}

    def fake_update_expiration_date(*, sudo="sudo"):
        calls["count"] += 1
        if calls["count"] == 1:
            return now - timezone.timedelta(minutes=1)
        certificate.expiration_date = now + timezone.timedelta(days=30)
        return certificate.expiration_date

    monkeypatch.setattr(certificate, "update_expiration_date", fake_update_expiration_date)
    monkeypatch.setattr(
        SelfSignedCertificate,
        "renew",
        lambda self, *, sudo="sudo": pytest.fail("renew should not run when no longer due"),
    )

    monkeypatch.setattr(
        "apps.certs.tasks.CertificateBase.objects.select_related",
        lambda *args, **kwargs: [certificate],
    )

    result = refresh_certificate_expirations()

    assert result == {"updated": 0, "renewed": 0}


@pytest.mark.django_db
def test_refresh_certificate_expirations_renews_when_still_due(monkeypatch):
    """Task renews due certificates after the second expiration validation."""
    now = timezone.now()
    certificate = SelfSignedCertificate.objects.create(
        name="recheck-renew",
        domain="renew.example.com",
        certificate_path="/tmp/renew/fullchain.pem",
        certificate_key_path="/tmp/renew/privkey.pem",
        expiration_date=now - timezone.timedelta(minutes=1),
        auto_renew=True,
    )

    monkeypatch.setattr(timezone, "now", lambda: now)
    monkeypatch.setattr(
        certificate,
        "update_expiration_date",
        lambda *, sudo="sudo": now - timezone.timedelta(minutes=1),
    )

    renewed = {"count": 0}

    def fake_renew(*, sudo="sudo"):
        renewed["count"] += 1
        return "renewed"

    monkeypatch.setattr(certificate, "renew", fake_renew)
    monkeypatch.setattr(
        "apps.certs.tasks.CertificateBase.objects.select_related",
        lambda *args, **kwargs: [certificate],
    )

    result = refresh_certificate_expirations()

    assert renewed["count"] == 1
    assert result == {"updated": 0, "renewed": 1}
