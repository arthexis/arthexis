"""Tests for Evergo public customer pages and artifact downloads."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoUser
from apps.evergo.views import _compute_tracking_step_completion
from apps.features.models import Feature


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
def test_order_tracking_public_remote_image_lookup_uses_fallback_sources_after_invalid_candidate(
    monkeypatch,
    client,
):
    """Regression: invalid values in earlier sources should not block valid fallback image URLs."""
    monkeypatch.setattr(
        EvergoUser,
        "fetch_order_detail",
        lambda self, *, order_id, timeout=20: {
        "reporte_visita": {"foto_tablero": {"placeholder": "not-a-url"}},
        "foto_tablero": "https://cdn.evergo.example/fotos/tablero-fallback.jpg",
        },
    )

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-image-fallback", email="owner-image-fallback@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-image-fallback@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30207, order_number="GM030207")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'src="https://cdn.evergo.example/fotos/tablero-fallback.jpg"' in content


@pytest.mark.django_db
def test_order_tracking_public_ignores_non_http_remote_image_urls(monkeypatch, client):
    """Security regression: preview images should only accept HTTP(S) URLs from Evergo."""
    monkeypatch.setattr(
        EvergoUser,
        "fetch_order_detail",
        lambda self, **_: {"foto_tablero": "javascript:alert(1)"},
    )

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-images-safe", email="owner-images-safe@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-images-safe@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30205, order_number="GM030205")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="preview-foto_tablero"' in content
    assert 'src="javascript:alert(1)"' not in content

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
def test_order_tracking_public_submits_with_missing_images_after_confirmation(monkeypatch, client):
    """Regression: tracking view should allow missing images after operator confirmation."""
    called = {"value": False}

    def _fake_submit(self, **kwargs):
        called["value"] = True
        return {"completed_steps": 4}

    monkeypatch.setattr(EvergoUser, "submit_tracking_phase_one", _fake_submit)

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
    assert called["value"] is True
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
def test_order_tracking_public_allows_partial_submission_without_required_primary_fields(monkeypatch, client):
    """Regression: operators can submit partial progress and continue later without completing every field."""
    called = {"value": False}

    def _fake_submit(self, **kwargs):
        called["value"] = True
        return {"completed_steps": 0}

    monkeypatch.setattr(EvergoUser, "submit_tracking_phase_one", _fake_submit)

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
    assert called["value"] is True
    content = response.content.decode()
    assert "Orden enviada correctamente. 0/4 pasos completados." in content
    assert "Orden enviada correctamente. 4/4 pasos completados." not in content

@pytest.mark.django_db
def test_order_tracking_public_shows_step_progress_with_incomplete_prefill(monkeypatch, client):
    """Regression: step summary should keep later steps pending when required fields remain missing."""
    monkeypatch.setattr(
        EvergoUser,
        "fetch_order_detail",
        lambda self, **_: {"reporte_visita": {"metraje_visita_tecnica": "31"}},
    )

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
def test_my_evergo_dashboard_fetches_missing_rows_when_partial_cache_exists(monkeypatch, client):
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

    loaded = {"value": False}

    def _fake_load(self, *, raw_queries, timeout=20):
        loaded["value"] = True
        EvergoOrder.objects.create(
            user=self,
            remote_id=28696,
            order_number="GM08888",
            client_name="Loaded From Sync",
        )
        return {
            "sales_orders": ["GM09999", "GM08888"],
            "customer_names": [],
            "customers_loaded": 1,
            "orders_created": 1,
            "orders_updated": 0,
            "placeholders_created": 0,
            "unresolved": [],
            "loaded_customer_ids": [],
            "loaded_order_ids": [],
        }

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", _fake_load)
    response = client.post(
        reverse("evergo:my-dashboard", kwargs={"token": profile.dashboard_token}),
        data={"raw_queries": "GM09999 GM08888"},
    )

    assert response.status_code == 200
    assert loaded["value"] is True
    assert "GM08888" in response.content.decode()

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
    """Security: TSV export should neutralize formulas and preserve table shape."""
    from apps.evergo.views import _to_tsv

    tsv = _to_tsv(
        [
            {
                "so": "=2+2",
                "customer_name": "Bob\nSmith",
                "status": "+new",
                "full_address": "A\tB",
                "phone": "@phone",
                "charger_brand": "-brand",
                "city": "Monterrey\rNL",
            }
        ]
    )

    assert "'=2+2" in tsv
    assert "Bob Smith" in tsv
    assert "'+new" in tsv
    assert "A B" in tsv
    assert "'@phone" in tsv
    assert "'-brand" in tsv
    assert "Monterrey NL" in tsv

@pytest.mark.django_db
def test_my_evergo_dashboard_404_for_invalid_token(client):
    """Security: dashboard should not be accessible with an unknown token."""
    response = client.get(reverse("evergo:my-dashboard", kwargs={"token": "00000000-0000-0000-0000-000000000000"}))

    assert response.status_code == 404
