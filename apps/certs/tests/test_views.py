import pytest

from django.urls import reverse

from apps.certs.models import SelfSignedCertificate
from apps.nginx.models import SiteConfiguration


pytestmark = pytest.mark.django_db


def _configure_default_site(settings, certificate):
    settings.ALLOWED_HOSTS = ["router.local", "testserver"]
    config = SiteConfiguration.get_default()
    config.certificate = certificate
    config.save(update_fields=["certificate"])
    return config


def test_trust_certificate_page_includes_download(admin_client, settings, tmp_path):
    certificate_path = tmp_path / "fullchain.pem"
    certificate_path.write_text("demo-cert", encoding="utf-8")
    key_path = tmp_path / "privkey.pem"
    key_path.write_text("demo-key", encoding="utf-8")

    certificate = SelfSignedCertificate.objects.create(
        name="router-cert",
        domain="router.local",
        certificate_path=str(certificate_path),
        certificate_key_path=str(key_path),
    )
    _configure_default_site(settings, certificate)

    response = admin_client.get(reverse("certs-trust"))

    assert response.status_code == 200
    assert b"Download certificate" in response.content


def test_trust_certificate_download_serves_pem(admin_client, settings, tmp_path):
    certificate_path = tmp_path / "fullchain.pem"
    certificate_path.write_text("demo-cert", encoding="utf-8")
    key_path = tmp_path / "privkey.pem"
    key_path.write_text("demo-key", encoding="utf-8")

    certificate = SelfSignedCertificate.objects.create(
        name="router-cert-download",
        domain="router.local",
        certificate_path=str(certificate_path),
        certificate_key_path=str(key_path),
    )
    _configure_default_site(settings, certificate)

    response = admin_client.get(reverse("certs-trust-download"))

    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert b"demo-cert" in content
