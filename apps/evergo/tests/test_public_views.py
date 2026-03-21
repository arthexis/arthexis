"""Focused Evergo public-view security regression tests."""

from __future__ import annotations

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
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_prefills_values_from_remote_order_payload(mock_fetch_order_detail, client):
    """Regression: tracking view should prefill phase-one values from WS order detail payload."""
    mock_fetch_order_detail.return_value = {
        "reporte_visita": {
            "metraje_visita_tecnica": "31",
            "programacion_cargador": "32A",
            "fecha_visita": "2026-03-10 13:45:00",
            "voltaje_fase_fase": "220.50",
            "numero_serie": "SER-REMOTE",
        }
    }

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-prefill", email="owner-prefill@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-prefill@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30199, order_number="GM030199")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["metraje_visita_tecnica"] == 31
    assert form.initial["programacion_cargador"] == "32A"
    assert form.initial["fecha_visita"] == "2026-03-10T13:45"
    assert str(form.initial["voltaje_fase_fase"]) == "220.50"
    assert form.initial["numero_serie"] == "SER-REMOTE"


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_prefers_nested_prefill_values_over_invalid_root_values(mock_fetch_order_detail, client):
    """Regression: invalid root payload values should not shadow valid nested prefill values."""
    mock_fetch_order_detail.return_value = {
        "metraje_visita_tecnica": "not-int",
        "reporte_visita": {
            "metraje_visita_tecnica": "31",
        },
    }

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-prefill-2", email="owner-prefill-2@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-prefill-2@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30200, order_number="GM030200")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["metraje_visita_tecnica"] == 31


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_prefill_localizes_timezone_aware_datetime_values(mock_fetch_order_detail, client):
    """Regression: timezone-aware payload datetimes should render in the configured local timezone."""
    mock_fetch_order_detail.return_value = {
        "reporte_visita": {
            "fecha_visita": "2026-03-10T13:45:00+00:00",
        }
    }

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-prefill-tz", email="owner-prefill-tz@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-prefill-tz@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30201, order_number="GM030201")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["fecha_visita"] == "2026-03-10T07:45"


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_loads_remote_image_previews(mock_fetch_order_detail, client):
    """Regression: tracking page should preload Evergo image URLs into preview elements."""
    mock_fetch_order_detail.return_value = {
        "reporte_visita": {
            "foto_tablero": "https://cdn.evergo.example/fotos/tablero.jpg",
        },
        "foto_medidor": {"url": "https://cdn.evergo.example/fotos/medidor.jpg"},
    }

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-images", email="owner-images@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-images@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30204, order_number="GM030204")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="preview-foto_tablero"' in content
    assert 'src="https://cdn.evergo.example/fotos/tablero.jpg"' in content
    assert 'src="https://cdn.evergo.example/fotos/medidor.jpg"' in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_counts_remote_images_for_step_status(mock_fetch_order_detail, client):
    """Regression: persisted remote images should count toward install/montage completion display."""
    mock_fetch_order_detail.return_value = {
        "reporte_visita": {
            "metraje_visita_tecnica": "10",
            "programacion_cargador": "32A",
            "capacidad_itm_principal": "60",
            "fecha_visita": "2026-02-26 13:00:00",
            "voltaje_fase_fase": "220",
            "voltaje_fase_tierra": "120",
            "voltaje_fase_neutro": "120",
            "voltaje_neutro_tierra": "1",
            "prueba_carga": "Sin prueba",
            "marca_cargador": "Marca",
            "numero_serie": "SER-1",
            "foto_tablero": "https://cdn.evergo.example/fotos/tablero.jpg",
            "foto_medidor": "https://cdn.evergo.example/fotos/medidor.jpg",
            "foto_tierra": "https://cdn.evergo.example/fotos/tierra.jpg",
            "foto_ruta_cableado": "https://cdn.evergo.example/fotos/ruta.jpg",
            "foto_ubicacion_cargador": "https://cdn.evergo.example/fotos/ubicacion.jpg",
            "foto_general": "https://cdn.evergo.example/fotos/general.jpg",
            "foto_hoja_visita": "https://cdn.evergo.example/fotos/hoja-visita.jpg",
            "foto_interruptor_principal": "https://cdn.evergo.example/fotos/interruptor-principal.jpg",
            "foto_panoramica_estacion": "https://cdn.evergo.example/fotos/panoramica.jpg",
            "foto_numero_serie_cargador": "https://cdn.evergo.example/fotos/serie-cargador.jpg",
            "foto_interruptor_instalado": "https://cdn.evergo.example/fotos/interruptor-instalado.jpg",
            "foto_conexion_cargador": "https://cdn.evergo.example/fotos/conexion.jpg",
            "foto_preparacion_cfe": "https://cdn.evergo.example/fotos/preparacion-cfe.jpg",
            "foto_hoja_reporte_instalacion": "https://cdn.evergo.example/fotos/hoja-reporte.jpg",
            "foto_voltaje_fase_fase": "https://cdn.evergo.example/fotos/vff.jpg",
            "foto_voltaje_fase_tierra": "https://cdn.evergo.example/fotos/vft.jpg",
            "foto_voltaje_fase_neutro": "https://cdn.evergo.example/fotos/vfn.jpg",
            "foto_voltaje_neutro_tierra": "https://cdn.evergo.example/fotos/vnt.jpg",
        }
    }

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-images-steps", email="owner-images-steps@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-images-steps@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30206, order_number="GM030206")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "✅ 3. Reporte de instalación" in content
    assert "✅ 4. Montaje-Conexión" in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={"foto_tablero": "javascript:alert(1)"})
def test_order_tracking_public_ignores_non_http_remote_image_urls(_, client):
    """Security regression: preview images should only accept HTTP(S) URLs from Evergo."""
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
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={})
def test_order_tracking_public_renders_feedback_and_chat_icons_when_enabled(_, client, settings):
    """Regression: tracking view should expose feedback/chat quick actions when enabled by permissions."""
    settings.PAGES_CHAT_ENABLED = True
    Feature.objects.update_or_create(
        slug="staff-chat-bridge",
        defaults={"display": "Staff Chat Bridge", "is_enabled": True},
    )
    Feature.objects.update_or_create(
        slug="feedback-ingestion",
        defaults={"display": "Feedback Ingestion", "is_enabled": True},
    )

    User = get_user_model()
    owner = User.objects.create_user(
        username="evergo-owner-icons",
        email="owner-icons@example.com",
        is_staff=True,
    )
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner-icons@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28700, order_number="GM01170")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="chat-launch"' in content
    assert 'id="user-story-toggle"' in content
    assert 'id="chat-widget"' in content
    assert 'id="user-story-overlay"' in content
    assert 'id="theme-toggle"' in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={})
def test_order_tracking_public_hides_feedback_and_chat_icons_when_disabled(_, client, settings):
    """Regression: tracking view should hide feedback/chat quick actions when permissions disable them."""
    settings.PAGES_CHAT_ENABLED = False
    Feature.objects.update_or_create(
        slug="feedback-ingestion",
        defaults={"display": "Feedback Ingestion", "is_enabled": False},
    )

    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-no-icons", email="owner-no-icons@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-no-icons@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28701, order_number="GM01171")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="chat-launch"' not in content
    assert 'id="user-story-toggle"' not in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", side_effect=OSError("timeout"))
def test_order_tracking_public_shows_field_level_prefill_errors_when_remote_fetch_fails(_, client):
    """Regression: remote prefill failures should be visible beside primary tracking inputs."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-prefill-errors", email="owner-prefill-errors@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-prefill-errors@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30202, order_number="GM030202")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "No se pudo cargar este dato desde Evergo API. Captúralo manualmente." in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={"reporte_visita": {"metraje_visita_tecnica": "31"}})
def test_order_tracking_public_shows_missing_field_prefill_errors_when_payload_incomplete(_, client):
    """Regression: incomplete prefill payloads should call out missing primary inputs."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-prefill-missing", email="owner-prefill-missing@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-prefill-missing@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30203, order_number="GM030203")
    client.force_login(owner)

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Dato faltante en Evergo API. Captúralo manualmente." in content




@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail", return_value={"foto_tablero": "https://cdn.evergo.example/fotos/tablero.jpg"})
def test_order_tracking_public_preserves_remote_previews_on_invalid_post(_, client):
    """Regression: invalid POST re-renders should keep remote image previews for operator context."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-invalid-post", email="owner-invalid-post@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner-invalid-post@example.com",
        evergo_password="secret",
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=30206, order_number="GM030206")
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
            # Do not confirm missing images to force non-redirect invalid form path.
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'src="https://cdn.evergo.example/fotos/tablero.jpg"' in content


@pytest.mark.django_db
@patch("apps.evergo.views.EvergoUser.fetch_order_detail")
def test_order_tracking_public_remote_image_lookup_uses_fallback_sources_after_invalid_candidate(
    mock_fetch_order_detail,
    client,
):
    """Regression: invalid values in earlier sources should not block valid fallback image URLs."""
    mock_fetch_order_detail.return_value = {
        "reporte_visita": {"foto_tablero": {"placeholder": "not-a-url"}},
        "foto_tablero": "https://cdn.evergo.example/fotos/tablero-fallback.jpg",
    }

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
def test_order_tracking_public_normalizes_common_status_text_artifacts(client):
    """Regression: tracking page should normalize known status encoding artifacts for display."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-owner-4b", email="owner4b@example.com")
    profile = EvergoUser.objects.create(user=owner, evergo_email="owner4b@example.com", evergo_password="secret")
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(
        user=profile,
        remote_id=28695,
        order_number="GM01195",
        status_name="Orden en ejecuci?n",
    )

    client.force_login(owner)
    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 200
    assert "Estatus: Orden en ejecución" in response.content.decode()


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
