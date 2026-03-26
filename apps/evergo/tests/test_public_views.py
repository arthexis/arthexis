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
