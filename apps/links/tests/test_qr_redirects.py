import pytest
from django.urls import reverse

from apps.links import models as links_models
from apps.links.models import QRRedirect, QRRedirectLead


@pytest.mark.django_db
def test_qr_redirect_save_strips_target_and_generates_slug():
    qr_redirect = QRRedirect.objects.create(target_url="  /qr-target/  ")

    assert qr_redirect.slug
    assert qr_redirect.target_url == "/qr-target/"


@pytest.mark.django_db
def test_qr_redirect_save_retries_on_slug_collision(monkeypatch):
    QRRedirect.objects.create(slug="collision", target_url="/existing/")
    slugs = iter(["collision", "unique-slug"])
    monkeypatch.setattr(links_models, "_generate_qr_slug", lambda: next(slugs))

    qr_redirect = QRRedirect(target_url="/new/")
    qr_redirect.save()

    assert qr_redirect.slug == "unique-slug"


@pytest.mark.django_db
def test_qr_redirect_public_view_creates_lead(client):
    qr_redirect = QRRedirect.objects.create(
        target_url="/destination/",
        is_public=True,
    )

    response = client.get(reverse("links:qr-redirect-public", args=[qr_redirect.slug]))

    assert response.status_code == 200
    lead = QRRedirectLead.objects.get(qr_redirect=qr_redirect)
    assert lead.target_url.startswith("http://testserver/destination/")


@pytest.mark.django_db
def test_qr_redirect_public_view_rejects_private_for_anonymous(client):
    qr_redirect = QRRedirect.objects.create(
        target_url="/destination/",
        is_public=False,
    )

    response = client.get(reverse("links:qr-redirect-public", args=[qr_redirect.slug]))

    assert response.status_code == 404
