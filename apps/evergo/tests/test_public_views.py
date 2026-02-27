"""Tests for Evergo public customer pages and artifact downloads."""

from __future__ import annotations

import pytest
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoUser


@pytest.mark.django_db
def test_customer_public_detail_renders_contact_map_and_artifacts(client):
    """Regression: public customer detail should expose summary fields and map link."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner", email="owner@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(
        user=profile,
        name="Jane Doe",
        phone_number="+52 555 1234",
        address="Av Siempre Viva 742, Monterrey, NL",
        latest_so="SO-123",
    )
    image = SimpleUploadedFile("house.png", b"img-bytes", content_type="image/png")
    pdf = SimpleUploadedFile("quote.pdf", b"%PDF-1.4\nmock", content_type="application/pdf")
    EvergoArtifact.objects.create(customer=customer, file=image)
    artifact_pdf = EvergoArtifact.objects.create(customer=customer, file=pdf)

    response = client.get(reverse("evergo:customer-public-detail", args=[customer.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "SO-123" in content
    assert "Jane Doe" in content
    assert "+52 555 1234" in content
    assert "Av Siempre Viva 742" in content
    assert "Open in Google Maps" in content
    assert "maps.google.com" in content
    assert "Last Order Number:" not in content
    assert "Full Name:" not in content
    assert "Phone Number:" not in content
    assert "Full Address:" not in content
    assert "A4 portrait" in content
    assert reverse("evergo:customer-artifact-download", args=[customer.pk, artifact_pdf.pk]) in content


@pytest.mark.django_db
def test_customer_artifact_download_rejects_non_pdf(client):
    """Regression: non-PDF attachments should not be downloadable from PDF endpoint."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-2", email="owner2@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner2@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="John")
    image = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("photo.jpg", b"img", content_type="image/jpeg"),
    )

    response = client.get(reverse("evergo:customer-artifact-download", args=[customer.pk, image.pk]))

    assert response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("filename", ["virus.exe", "spreadsheet.xlsx"])
def test_evergo_artifact_validation_blocks_unsupported_file_extensions(filename):
    """Regression: artifact model should only allow image and PDF file types."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-3", email="owner3@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner3@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="Jane")

    with pytest.raises(ValidationError):
        EvergoArtifact.objects.create(
            customer=customer,
            file=SimpleUploadedFile(filename, b"bad"),
        )


@pytest.mark.django_db
def test_order_tracking_public_renders_defaults(client):
    """Regression: public order tracking should render required phase-one controls."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-4", email="owner4@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner4@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28690, order_number="GM01162")

    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Metraje visita técnica" in content
    assert "Enviar Visita + Asignar + Instalación" in content
    assert "Programacion cargador" in content
    assert "https://portal-mex.evergo.com/ordenes/28690" in content


@pytest.mark.django_db
def test_order_tracking_public_requires_login(client):
    """Security: anonymous users should be redirected to login for tracking form access."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-6", email="owner6@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner6@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28692, order_number="GM01164")

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_order_tracking_public_rejects_non_owner_access(client):
    """Security: authenticated users cannot access tracking forms for other users' orders."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-7", email="owner7@example.com")
    intruder = User.objects.create_user(username="evergo-owner-8", email="owner8@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner7@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28693, order_number="GM01165")

    client.force_login(intruder)
    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 404


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.submit_tracking_phase_one", return_value={"completed_steps": 4})
def test_order_tracking_public_submits_with_missing_images_after_confirmation(mock_submit, client):
    """Regression: tracking view should allow missing images after operator confirmation."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-5", email="owner5@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner5@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28691, order_number="GM01163")

    client.force_login(owner)

    response = client.post(
        reverse("evergo:order-tracking-public", args=[order.remote_id]),
        data={
            "metraje_visita_tecnica": 10,
            "voltaje_fase_fase": "220",
            "voltaje_fase_tierra": "120",
            "voltaje_fase_neutro": "120",
            "voltaje_neutro_tierra": "1",
            "capacidad_itm_principal": 60,
            "programacion_cargador": "32A",
            "fecha_visita": "2026-02-26T13:00",
            "marca_cargador": "",
            "numero_serie": "SER-1",
            "prueba_carga": "Sin prueba",
            "confirm_missing_images": "1",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert mock_submit.called
    assert "4/4 pasos completados" in response.content.decode()


@pytest.mark.django_db
def test_my_evergo_dashboard_renders_and_generates_table_from_local_orders(client):
    """Regression: dashboard token page should render readonly username and order table rows."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-owner", email="dash@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="dash@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    EvergoOrder.objects.create(
        user=profile,
        remote_id=28690,
        order_number="GM01162",
        client_name="Jane Doe",
        status_name="Asignada",
        address_street="Av Reforma",
        address_num_ext="10",
        address_municipality="Monterrey",
        phone_primary="+52 555 9999",
        site_name="Tesla",
    )

    response = client.post(
        reverse("evergo:my-dashboard", kwargs={"token": profile.dashboard_token}),
        data={"raw_queries": "GM01162"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "My Evergo Dashboard" in content
    assert "evergo-dashboard-owner" in content
    assert "read only" in content
    assert "GM01162" in content
    assert "Jane Doe" in content
    assert "Monterrey" in content
    assert "https://portal-mex.evergo.com/ordenes/28690" in content
    assert "Copy / Paste table" in content


@pytest.mark.django_db
def test_my_evergo_dashboard_404_for_invalid_token(client):
    """Security: dashboard should not be accessible with an unknown token."""
    response = client.get(reverse("evergo:my-dashboard", kwargs={"token": "00000000-0000-0000-0000-000000000000"}))

    assert response.status_code == 404
