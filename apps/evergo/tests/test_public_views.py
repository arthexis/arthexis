"""Focused Evergo public-view security regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoUser
from apps.evergo.views import _compute_tracking_step_completion
from apps.features.models import Feature

@pytest.mark.django_db
def test_customer_public_detail_requires_authentication(client):
    """Regression: customer detail should require login for access."""
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

    response = client.get(reverse("evergo:customer-public-detail", args=[customer.pk]))

    assert response.status_code == 302
    assert "/login/" in response.url

@pytest.mark.django_db
def test_customer_public_detail_renders_for_authenticated_owner(client):
    """Regression: customer detail should still render for the owning authenticated user."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-detail", email="owner-detail@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-detail@example.com",
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

    client.force_login(owner)
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
def test_customer_public_detail_rejects_non_owner_access(client):
    """Security: authenticated users cannot view customer details for other owners."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-private", email="owner-private@example.com")
    intruder = User.objects.create_user(username="evergo-intruder", email="intruder@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-private@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="Jane Doe")

    client.force_login(intruder)
    response = client.get(reverse("evergo:customer-public-detail", args=[customer.pk]))

    assert response.status_code == 404

@pytest.mark.django_db
def test_customer_artifact_download_requires_authentication(client):
    """Regression: artifact download should require login before evaluating the artifact."""
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

    assert response.status_code == 302
    assert "/login/" in response.url

@pytest.mark.django_db
def test_customer_artifact_download_rejects_non_pdf_for_authenticated_owner(client):
    """Regression: authenticated owners should still get 404 for non-PDF downloads."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-2b", email="owner2b@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner2b@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="John")
    image = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("photo.jpg", b"img", content_type="image/jpeg"),
    )

    client.force_login(owner)
    response = client.get(reverse("evergo:customer-artifact-download", args=[customer.pk, image.pk]))

    assert response.status_code == 404

@pytest.mark.django_db
def test_customer_artifact_download_rejects_non_owner_access(client):
    """Security: authenticated users cannot download artifacts for other owners' customers."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-2c", email="owner2c@example.com")
    intruder = User.objects.create_user(username="evergo-owner-2d", email="owner2d@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner2c@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="John")
    pdf = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("quote.pdf", b"%PDF-1.4\nmock", content_type="application/pdf"),
    )

    client.force_login(intruder)
    response = client.get(reverse("evergo:customer-artifact-download", args=[customer.pk, pdf.pk]))

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

def test_compute_tracking_step_completion_allows_assign_without_visita_completion():
    """Regression: assign step can be complete independently while install still requires visita completion."""
    completion = _compute_tracking_step_completion(
        {
            "fecha_visita": "2026-03-09T10:00",
            "programacion_cargador": "32A",
        }
    )

    assert completion["visita"] is False
    assert completion["assign"] is True
    assert completion["install"] is False
    assert completion["montage"] is False

@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.submit_tracking_phase_one", return_value={"completed_steps": 1})
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={"reporte_visita": {"foto_tablero": "https://cdn.evergo.example/fotos/tablero.jpg"}})
def test_order_tracking_public_preserves_remote_images_on_partial_submission(_, mock_submit, client):
    """Regression: partial submissions should not overwrite already persisted remote images with placeholders."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-remote-images", email="owner-remote-images@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-remote-images@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30316, order_number="GM030316")
    client.force_login(owner)

    response = client.post(
        reverse("evergo:order-tracking-public", args=[order.remote_id]),
        data={
            "metraje_visita_tecnica": 10,
            "confirm_missing_images": "1",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert mock_submit.called
    files = mock_submit.call_args.kwargs["files"]
    assert "foto_tablero" not in files

@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.submit_tracking_phase_one", return_value={"completed_steps": 0})
def test_order_tracking_public_allows_partial_submission_without_required_primary_fields(mock_submit, client):
    """Regression: operators can submit partial progress and continue later without completing every field."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-partial", email="owner-partial@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-partial@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30314, order_number="GM030314")
    client.force_login(owner)

    response = client.post(
        reverse("evergo:order-tracking-public", args=[order.remote_id]),
        data={
            "metraje_visita_tecnica": 10,
            "confirm_missing_images": "1",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert mock_submit.called
    content = response.content.decode()
    assert "Orden enviada correctamente. 0/4 pasos completados." in content
    assert "Orden enviada correctamente. 4/4 pasos completados." not in content

@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={"reporte_visita": {"metraje_visita_tecnica": "31"}})
def test_order_tracking_public_shows_step_progress_with_incomplete_prefill(_, client):
    """Regression: step summary should keep later steps pending when required fields remain missing."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-step-status", email="owner-step-status@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-step-status@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30315, order_number="GM030315")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Estado de pasos" in content
    assert "🕓 1. Visita técnica" in content

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
def test_my_evergo_dashboard_fetches_missing_rows_when_partial_cache_exists(client):
    """Regression: mixed cached + uncached lookups should still trigger API sync."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-owner-2", email="dash2@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="dash2@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    EvergoOrder.objects.create(
        user=profile,
        remote_id=28695,
        order_number="GM09999",
        client_name="Jane Doe",
    )

    with patch("apps.evergo.views.EvergoUser.load_customers_from_queries") as mock_load:
        response = client.post(
            reverse("evergo:my-dashboard", kwargs={"token": profile.dashboard_token}),
            data={"raw_queries": "GM09999 GM08888"},
        )

    assert response.status_code == 200
    assert mock_load.called

@pytest.mark.django_db
def test_my_evergo_dashboard_shows_validation_errors_for_large_query_payload(client):
    """Regression: dashboard should render field errors for invalid raw query submissions."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-owner-3", email="dash3@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="dash3@example.com", evergo_password="secret")

    response = client.post(
        reverse("evergo:my-dashboard", kwargs={"token": profile.dashboard_token}),
        data={"raw_queries": "x " * 101},
    )

    assert response.status_code == 200
    assert "Too many values in raw_queries" in response.content.decode()

@pytest.mark.django_db
def test_my_evergo_dashboard_handles_orders_without_remote_id(client):
    """Regression: dashboard rows should be null-safe when remote_id is absent."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-owner-4", email="dash4@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="dash4@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    EvergoOrder.objects.create(
        user=profile,
        remote_id=None,
        order_number="",
        client_name="No Remote",
        status_name="Pendiente",
    )

    response = client.post(
        reverse("evergo:my-dashboard", kwargs={"token": profile.dashboard_token}),
        data={"raw_queries": "No Remote"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "No Remote" in content
    assert "&gt;-&lt;" not in content
    assert ">-</a>" in content
    assert "portal-mex.evergo.com/ordenes/None" not in content

def test_to_tsv_sanitizes_formula_and_line_break_characters():
    """Security: TSV export must neutralize formulas and sanitize control characters."""

    from apps.evergo.views import _to_tsv

    tsv = _to_tsv(
        [
            {
                "so": "=2+2",
                "customer_name": "Bob\nSmith",
                "status": " +new",
                "full_address": "A\tB",
                "phone": "\t@phone",
                "charger_brand": "-brand",
                "city": "Monterrey\rNL",
            }
        ]
    )

    assert "'=2+2" in tsv
    assert "Bob Smith" in tsv
    assert "' +new" in tsv
    assert "A B" in tsv
    assert "' @phone" in tsv
    assert "'-brand" in tsv
    assert "Monterrey NL" in tsv
