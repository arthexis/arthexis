"""Admin tests for the Evergo integration app."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoUser


@pytest.fixture
def evergo_customer_export_record(db):
    """Create a customer export fixture with full owner/profile graph."""

    user_model = get_user_model()

    def _create(*, username, email, remote_id, name):
        owner = user_model.objects.create_user(username=username, email=email)
        profile = EvergoUser.objects.create(
            user=owner,
            evergo_email=email,
            evergo_password="secret",  # noqa: S106
        )
        return EvergoCustomer.objects.create(
            user=profile,
            remote_id=remote_id,
            name=name,
            email=email,
        )

    return _create


@pytest.mark.django_db
def test_evergo_admin_load_customers_tool_action_is_registered_on_customers_only(admin_client):
    """Load-customers tool action should be exposed only for customers."""

    customer_tool_url = reverse("admin:evergo_evergocustomer_actions", args=["load_customers_wizard"])
    response = admin_client.get(customer_tool_url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:evergo_evergocustomer_load_customers")

    contractor_tool_url = reverse("admin:evergo_evergouser_actions", args=["load_customers_wizard"])
    contractor_response = admin_client.get(contractor_tool_url)
    assert contractor_response.status_code == 404


@pytest.mark.django_db
def test_evergo_admin_load_orders_and_load_customers_actions_redirect_to_shared_wizard(admin_client):
    """Load orders and load customers actions should point to the same wizard."""

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")

    load_orders_action_url = reverse("admin:evergo_evergoorder_actions", args=["load_orders_wizard"])
    load_customers_action_url = reverse("admin:evergo_evergocustomer_actions", args=["load_customers_wizard"])

    load_orders_response = admin_client.get(load_orders_action_url)
    load_customers_response = admin_client.get(load_customers_action_url)

    assert load_orders_response.status_code == 302
    assert load_orders_response["Location"] == wizard_url
    assert load_customers_response.status_code == 302
    assert load_customers_response["Location"] == wizard_url


@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.load_customers_from_queries")
def test_evergo_admin_load_customers_wizard_submits(mock_load_customers, admin_client):
    """Wizard submit should invoke sync for selected profile and redirect."""

    mock_load_customers.return_value = {
        "customers_loaded": 1,
        "orders_created": 1,
        "orders_updated": 0,
        "placeholders_created": 0,
        "unresolved": [],
    }
    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="suite-tool@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    get_response = admin_client.get(wizard_url)
    assert get_response.status_code == 200

    post_response = admin_client.post(
        wizard_url,
        {"profile": profile.pk, "raw_queries": "J00830, Customer Name"},
    )
    assert post_response.status_code == 302
    mock_load_customers.assert_called_once_with(raw_queries="J00830, Customer Name")


@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.load_customers_from_queries")
def test_evergo_admin_load_customers_wizard_rejects_unowned_profile(mock_load_customers, admin_client):
    """Wizard should not allow selecting a profile owned by another user."""

    user_model = get_user_model()
    other_user = user_model.objects.create_user(
        username="other-evergo-owner",
        email="other-evergo-owner@example.com",
    )
    other_profile = EvergoUser.objects.create(
        user=other_user,
        evergo_email="other-profile@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.post(
        wizard_url,
        {"profile": other_profile.pk, "raw_queries": "J00830"},
    )

    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    mock_load_customers.assert_not_called()


@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.test_login")
def test_evergo_admin_change_action_runs_test_login_sync(mock_test_login, admin_client):
    """Change-form action should run login sync for a selected Evergo user."""

    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-change-action",
        email="suite-admin-change-action@example.com",
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-change-action@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    action_url = reverse(
        "admin:evergo_evergouser_actions",
        args=[profile.pk, "test_login_and_sync_action"],
    )
    response = admin_client.post(action_url, follow=True)

    assert response.status_code == 200
    mock_test_login.assert_called_once_with()


@pytest.mark.django_db
def test_evergo_customer_export_view_honors_column_selection_for_tsv(
    admin_client, evergo_customer_export_record
):
    """TSV export should include only requested columns in request order."""

    evergo_customer_export_record(
        username="suite-admin-export-tsv",
        email="suite-admin-export-tsv@example.com",
        remote_id=7002,
        name="TSV Export User",
    )

    export_url = reverse("admin:evergo_evergocustomer_export")
    response = admin_client.post(
        export_url,
        {
            "format": "tsv",
            "export_columns": ["remote_id", "name"],
        },
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/tab-separated-values")
    rows = response.content.decode("utf-8").strip().splitlines()
    assert rows[0] == "remote_id\tname"
    assert rows[1].startswith("7002\tTSV Export User")


@pytest.mark.django_db
def test_evergo_customer_export_view_rejects_empty_column_selection(
    admin_client, evergo_customer_export_record
):
    """Exporting without selected columns should fail with explicit error."""

    evergo_customer_export_record(
        username="suite-admin-export-empty",
        email="suite-admin-export-empty@example.com",
        remote_id=7003,
        name="No Columns",
    )

    export_url = reverse("admin:evergo_evergocustomer_export")
    response = admin_client.post(export_url, {"format": "tsv"})

    assert response.status_code == 400
    assert "Select at least one column" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_evergo_customer_admin_get_queryset_limits_non_superuser_visibility():
    """Non-superusers should only see customers for their own Evergo profile."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-owner-visible",
        email="suite-admin-owner-visible@example.com",
        is_staff=True,
    )
    other_owner = user_model.objects.create_user(
        username="suite-admin-owner-hidden",
        email="suite-admin-owner-hidden@example.com",
        is_staff=True,
    )

    owner_profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-owner-visible@example.com",
        evergo_password="secret",  # noqa: S106
    )
    other_profile = EvergoUser.objects.create(
        user=other_owner,
        evergo_email="suite-admin-owner-hidden@example.com",
        evergo_password="secret",  # noqa: S106
    )

    visible_customer = EvergoCustomer.objects.create(user=owner_profile, name="Visible Customer")
    EvergoCustomer.objects.create(user=other_profile, name="Hidden Customer")

    model_admin = admin.site._registry[EvergoCustomer]
    request = RequestFactory().get(reverse("admin:evergo_evergocustomer_changelist"))
    request.user = owner

    queryset = model_admin.get_queryset(request)

    assert list(queryset.values_list("id", flat=True)) == [visible_customer.id]


@pytest.mark.django_db
def test_evergo_order_admin_get_queryset_limits_non_superuser_visibility():
    """Non-superusers should only see orders for their own Evergo profile."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-order-owner-visible",
        email="suite-admin-order-owner-visible@example.com",
        is_staff=True,
    )
    other_owner = user_model.objects.create_user(
        username="suite-admin-order-owner-hidden",
        email="suite-admin-order-owner-hidden@example.com",
        is_staff=True,
    )

    owner_profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-order-owner-visible@example.com",
        evergo_password="secret",  # noqa: S106
    )
    other_profile = EvergoUser.objects.create(
        user=other_owner,
        evergo_email="suite-admin-order-owner-hidden@example.com",
        evergo_password="secret",  # noqa: S106
    )

    visible_order = EvergoOrder.objects.create(user=owner_profile, remote_id=9911, order_number="SO-9911")
    EvergoOrder.objects.create(user=other_profile, remote_id=9912, order_number="SO-9912")

    model_admin = admin.site._registry[EvergoOrder]
    request = RequestFactory().get(reverse("admin:evergo_evergoorder_changelist"))
    request.user = owner

    queryset = model_admin.get_queryset(request)

    assert list(queryset.values_list("id", flat=True)) == [visible_order.id]


@pytest.mark.django_db
def test_evergo_admin_load_customers_wizard_load_all_button_requires_confirmation(admin_client):
    """Load-all action should require explicit user confirmation in UI."""

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.get(wizard_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Load all customers" in content
    assert "return confirm('This will sync every customer available to this profile. Continue?');" in content
