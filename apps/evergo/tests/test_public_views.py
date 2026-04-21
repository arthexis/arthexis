"""Focused Evergo public-view regression tests."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoUser


def _create_customer(*, username: str = "evergo-owner") -> EvergoCustomer:
    user_model = get_user_model()
    owner = user_model.objects.create_user(username=username, email=f"{username}@example.com")
    owner_profile = EvergoUser.objects.create(
        user=owner,
        evergo_email=f"{username}@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )
    return EvergoCustomer.objects.create(
        user=owner_profile,
        name="Public Customer",
        address="Monterrey, NL",
        latest_so="SO-777",
    )


@pytest.mark.django_db
def test_order_tracking_public_requires_login(client):
    """Security: anonymous users should be redirected to login for tracking form access."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner-6",
        email="owner6@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner6@example.com",
        evergo_password="secret",  # noqa: S106
    )
    from apps.evergo.models import EvergoOrder

    order = EvergoOrder.objects.create(user=profile, remote_id=28692, order_number="GM01164")

    response = client.get(reverse("evergo:order-tracking-public", args=[order.remote_id]))

    assert response.status_code == 302
    assert "login" in response["Location"]


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


@pytest.mark.django_db
def test_customer_public_detail_allows_anonymous_access(client):
    customer = _create_customer()

    response = client.get(reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
    assert "Download PDF" in response.content.decode()


@pytest.mark.django_db
def test_customer_public_detail_does_not_expose_google_maps_api_key(client, settings):
    settings.GOOGLE_MAPS_API_KEY = "super-secret-key"
    customer = _create_customer(username="owner-map-key")

    response = client.get(reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "maps.googleapis.com/maps/api/staticmap" in content
    assert "super-secret-key" not in content
    assert "&key=" not in content


@pytest.mark.django_db
def test_customer_public_detail_uploads_and_deletes_image(client):
    customer = _create_customer(username="owner-with-image")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    upload_response = client.post(
        detail_url,
        data={
            "action": "upload-image",
            "image": SimpleUploadedFile(
                "evidence.jpg",
                b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b",
                content_type="image/gif",
            ),
        },
    )

    assert upload_response.status_code == 302
    artifact = EvergoArtifact.objects.get(customer=customer)
    storage_name = artifact.file.name

    delete_response = client.post(
        detail_url,
        data={"action": "delete-image", "artifact_id": artifact.pk, "confirm_delete": "yes"},
    )

    assert delete_response.status_code == 302
    assert not EvergoArtifact.objects.filter(customer=customer).exists()
    assert not artifact.file.storage.exists(storage_name)


@pytest.mark.django_db
def test_customer_public_detail_delete_image_with_invalid_artifact_id_returns_404(client):
    customer = _create_customer(username="owner-invalid-artifact-id")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    response = client.post(
        detail_url,
        data={"action": "delete-image", "artifact_id": "abc", "confirm_delete": "yes"},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_customer_public_detail_delete_image_with_unicode_numeric_artifact_id_returns_404(client):
    customer = _create_customer(username="owner-invalid-unicode-artifact-id")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    response = client.post(
        detail_url,
        data={"action": "delete-image", "artifact_id": "²", "confirm_delete": "yes"},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_customer_public_detail_enforces_image_and_storage_limits(client, settings):
    settings.EVERGO_PUBLIC_IMAGE_LIMIT = 1
    settings.EVERGO_PUBLIC_IMAGE_TOTAL_STORAGE_LIMIT = 50
    customer = _create_customer(username="owner-limits")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    gif_payload = (
        b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02"
        b"\x02\x44\x01\x00\x3b"
    )
    EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("existing.gif", gif_payload, content_type="image/gif"),
        artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE,
        display_order=1,
    )

    response = client.post(
        detail_url,
        data={
            "action": "upload-image",
            "image": SimpleUploadedFile("new.gif", gif_payload, content_type="image/gif"),
        },
    )

    assert response.status_code == 200
    assert "You can only add up to 1 images." in response.content.decode()


@pytest.mark.django_db
def test_customer_public_detail_handles_artifact_model_validation_error(client, monkeypatch):
    customer = _create_customer(username="owner-validation-error")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    def _raise_validation_error(**_kwargs):
        raise ValidationError({"file": ["Only image files and PDFs are allowed."]})

    monkeypatch.setattr("apps.evergo.views.EvergoArtifact.objects.create", _raise_validation_error)

    response = client.post(
        detail_url,
        data={
            "action": "upload-image",
            "image": SimpleUploadedFile(
                "photo.jpg",
                b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b",
                content_type="image/gif",
            ),
        },
    )

    assert response.status_code == 200
    assert "Only image files and PDFs are allowed." in response.content.decode()
    assert not EvergoArtifact.objects.filter(customer=customer).exists()


@pytest.mark.django_db
def test_customer_public_detail_rejects_upload_that_resolves_to_non_image_artifact_type(client):
    customer = _create_customer(username="owner-non-image-artifact")
    detail_url = reverse("evergo:customer-public-detail", kwargs={"public_id": customer.public_id})

    response = client.post(
        detail_url,
        data={
            "action": "upload-image",
            "image": SimpleUploadedFile(
                "not-image.pdf",
                b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b",
                content_type="image/gif",
            ),
        },
    )

    assert response.status_code == 200
    assert "Only image files are allowed for this upload." in response.content.decode()
    assert not EvergoArtifact.objects.filter(customer=customer).exists()


@pytest.mark.django_db
def test_customer_pdf_download_generates_pdf_without_deleting_uploaded_images(client, monkeypatch):
    customer = _create_customer(username="owner-pdf")
    artifact = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("one.jpg", b"12345", content_type="image/jpeg"),
        artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE,
        display_order=1,
    )
    storage_name = artifact.file.name

    monkeypatch.setattr("apps.evergo.views._remote_image_data_uri", lambda _: "")
    monkeypatch.setattr("apps.evergo.views._render_pdf_bytes", lambda html: b"%PDF-1.4 fake")

    response = client.get(reverse("evergo:customer-pdf-download", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert EvergoArtifact.objects.filter(customer=customer).exists()
    assert artifact.file.storage.exists(storage_name)


@pytest.mark.django_db
def test_customer_pdf_download_passes_data_uris_to_template(client, monkeypatch):
    customer = _create_customer(username="owner-pdf-data-uri")
    EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("one.jpg", b"12345", content_type="image/jpeg"),
        artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE,
        display_order=1,
    )
    captured_context = {}

    def _capture_template(_template_name, context):
        captured_context.update(context)
        return "<html></html>"

    monkeypatch.setattr("apps.evergo.views._remote_image_data_uri", lambda _: "data:image/png;base64,YWJj")
    monkeypatch.setattr("apps.evergo.views.render_to_string", _capture_template)
    monkeypatch.setattr("apps.evergo.views._render_pdf_bytes", lambda html: b"%PDF-1.4 fake")

    response = client.get(reverse("evergo:customer-pdf-download", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
    assert captured_context["google_maps_snapshot_data_uri"].startswith("data:image/png;base64,")
    assert captured_context["image_artifacts"][0]["url"].startswith("data:image/")


@pytest.mark.django_db
def test_customer_pdf_download_sanitizes_content_disposition_filename(client, monkeypatch):
    customer = _create_customer(username="owner-pdf-filename")
    customer.latest_so = "SO\r\nbad"
    customer.save(update_fields=["latest_so"])
    monkeypatch.setattr("apps.evergo.views._remote_image_data_uri", lambda _: "")
    monkeypatch.setattr("apps.evergo.views._render_pdf_bytes", lambda html: b"%PDF-1.4 fake")

    response = client.get(reverse("evergo:customer-pdf-download", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="evergo-SObad.pdf"'


@pytest.mark.django_db
def test_customer_pdf_download_not_blocked_by_reports_pdf_toggle(client, monkeypatch, settings):
    settings.REPORTS_HTML_TO_PDF_ENABLED = False
    settings.EVERGO_PUBLIC_HTML_TO_PDF_ENABLED = True
    customer = _create_customer(username="owner-pdf-toggle")
    monkeypatch.setattr("apps.evergo.views._remote_image_data_uri", lambda _: "")
    monkeypatch.setattr("apps.evergo.views._render_pdf_bytes", lambda html: b"%PDF-1.4 fake")

    response = client.get(reverse("evergo:customer-pdf-download", kwargs={"public_id": customer.public_id}))

    assert response.status_code == 200
