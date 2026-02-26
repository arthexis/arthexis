"""Tests for Evergo public customer pages and artifact downloads."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoOrder, EvergoUser


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
def test_my_dashboard_renders_username_external_link_and_orders_table(client):
    """Regression: dashboard should render operator table and external orders link."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard", email="dash@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="dash@example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoOrder.objects.create(
        user=profile,
        remote_id=9001,
        order_number="J00830",
        client_name="Irma Ravize",
        status_name="Pendiente",
        phone_primary="8115889790",
        site_name="Chevrolet",
        address_street="Capellania",
        address_num_ext="107",
        address_neighborhood="Centro",
        address_municipality="Apodaca",
        address_state="NL",
        address_postal_code="66600",
    )

    dashboard_url = profile.dashboard_public_url()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            EvergoUser,
            "load_customers_from_queries",
            lambda self, *, raw_queries, timeout=20: (_ for _ in ()).throw(AssertionError("should not call API when local SO exists")),
        )
        response = client.post(dashboard_url, {"sales_orders": "J00830"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "My Evergo Dashboard" in content
    assert "evergo-dashboard" in content
    assert "target=\"_blank\"" in content
    assert "J00830" in content
    assert "Irma Ravize" in content
    assert "Chevrolet" in content
    assert "Apodaca" in content


@pytest.mark.django_db
def test_my_dashboard_rejects_invalid_token(client):
    """Regression: dashboard URL should reject malformed or expired signatures."""
    response = client.get(reverse("evergo:my-dashboard", args=["invalid-token"]))

    assert response.status_code == 200
    assert "invalid or has expired" in response.content.decode().lower()


@pytest.mark.django_db
def test_my_dashboard_renders_tsv_copy_block(client):
    """Regression: dashboard should render TSV block for quick copy/paste."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-tsv", email="dash-tsv@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="dash-tsv@example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoOrder.objects.create(
        user=profile,
        remote_id=9002,
        order_number="GM01321",
        client_name="Jesus Cortez",
        status_name="Programada",
        phone_primary="8111111111",
        site_name="Chevrolet",
        address_street="Santa Barbara",
        address_num_ext="404",
        address_municipality="Apodaca",
        address_state="NL",
        address_postal_code="66647",
    )

    response = client.post(profile.dashboard_public_url(), {"sales_orders": "GM01321"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "Copy / Paste Table (TSV)" in content
    assert "SO\tCustomer Name\tStatus" in content
    assert "GM01321\tJesus Cortez\tProgramada" in content


@pytest.mark.django_db
def test_my_dashboard_supports_username_token_latest_order_lookup(client):
    """Regression: dashboard @username queries should resolve latest local order."""
    User = get_user_model()
    owner = User.objects.create_user(username="evergo-dashboard-user", email="dash-user@example.com")
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="dash-user@example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoOrder.objects.create(
        user=profile,
        remote_id=9100,
        order_number="GM01320",
        client_name="Irma Ravize",
        source_updated_at=timezone.now() - timezone.timedelta(days=2),
    )
    EvergoOrder.objects.create(
        user=profile,
        remote_id=9101,
        order_number="GM01321",
        client_name="Irma Ravize",
        source_updated_at=timezone.now() - timezone.timedelta(days=1),
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            EvergoUser,
            "load_customers_from_queries",
            lambda self, *, raw_queries, timeout=20: (_ for _ in ()).throw(AssertionError("should not call API when username is local")),
        )
        response = client.post(profile.dashboard_public_url(), {"sales_orders": "@irma"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "GM01321" in content
    assert "GM01320" not in content
