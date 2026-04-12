"""Focused Evergo public-view security regression tests."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evergo.models import EvergoArtifact, EvergoCustomer, EvergoUser


@pytest.mark.django_db
def test_customer_public_detail_requires_authentication(client):
    """Regression: customer detail should require login for access."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner",
        email="owner@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(
        user=profile,
        name="Jane Doe",
    )

    response = client.get(reverse("evergo:customer-public-detail", args=[customer.pk]))

    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_customer_public_detail_rejects_non_owner_access(client):
    """Security: authenticated users cannot view customer details for other owners."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner-private",
        email="owner-private@example.com",
    )
    intruder = user_model.objects.create_user(
        username="evergo-intruder",
        email="intruder@example.com",
    )
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
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner-2",
        email="owner2@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner2@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="John")
    artifact = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("photo.jpg", b"img", content_type="image/jpeg"),
    )

    response = client.get(
        reverse("evergo:customer-artifact-download", args=[customer.pk, artifact.pk])
    )

    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_customer_artifact_download_rejects_non_owner_access(client):
    """Security: authenticated users cannot download artifacts for other owners' customers."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner-2c",
        email="owner2c@example.com",
    )
    intruder = user_model.objects.create_user(
        username="evergo-owner-2d",
        email="owner2d@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner2c@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="John")
    artifact = EvergoArtifact.objects.create(
        customer=customer,
        file=SimpleUploadedFile("quote.pdf", b"%PDF-1.4\nmock", content_type="application/pdf"),
    )

    client.force_login(intruder)
    response = client.get(
        reverse("evergo:customer-artifact-download", args=[customer.pk, artifact.pk])
    )

    assert response.status_code == 404


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
def test_order_tracking_public_rejects_non_owner_access(client):
    """Security: authenticated users cannot access tracking forms for other users' orders."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="evergo-owner-7",
        email="owner7@example.com",
    )
    intruder = user_model.objects.create_user(
        username="evergo-owner-8",
        email="owner8@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="owner7@example.com",
        evergo_password="secret",  # noqa: S106
    )
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
