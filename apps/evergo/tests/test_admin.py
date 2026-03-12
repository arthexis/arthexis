"""Admin tests for the Evergo integration app."""

from __future__ import annotations

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
def test_evergo_admin_load_customers_wizard_submits(monkeypatch, admin_client):
    """Wizard submit should invoke sync for selected profile and redirect."""

    def _fake_loader(self, *, raw_queries, timeout=20):
        assert raw_queries == "J00830, Customer Name"
        EvergoOrder.objects.create(user=self, remote_id=202, order_number="J00830")
        return {
            "customers_loaded": 1,
            "orders_created": 1,
            "orders_updated": 0,
            "placeholders_created": 0,
            "unresolved": [],
            "loaded_customer_ids": [101],
            "loaded_order_ids": [202],
        }

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", _fake_loader)
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
    assert post_response["Location"].endswith("/admin/evergo/evergoorder/?id__in=202")


@pytest.mark.django_db
def test_evergo_admin_load_customers_wizard_can_redirect_to_customers_with_selected_ids(
    monkeypatch, admin_client
):
    """Regression: wizard next-view selector should support customer destination with scoped IDs."""
    loaded_customer_ids: list[int] = []

    def _fake_loader(self, *, raw_queries, timeout=20):
        first = EvergoCustomer.objects.create(user=self, remote_id=12, name="Customer A")
        second = EvergoCustomer.objects.create(user=self, remote_id=18, name="Customer B")
        EvergoOrder.objects.create(user=self, remote_id=99, order_number="J00830")
        loaded_customer_ids.extend([first.pk, second.pk])
        return {
            "customers_loaded": 2,
            "orders_created": 2,
            "orders_updated": 0,
            "placeholders_created": 0,
            "unresolved": [],
            "loaded_customer_ids": [first.pk, second.pk],
            "loaded_order_ids": [99],
        }

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", _fake_loader)
    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="suite-tool-customers@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.post(
        wizard_url,
        {
            "profile": profile.pk,
            "raw_queries": "J00830",
            "next_view": "customers",
        },
    )

    assert response.status_code == 302
    assert response["Location"].endswith(
        f"/admin/evergo/evergocustomer/?id__in={loaded_customer_ids[0]},{loaded_customer_ids[1]}"
    )


@pytest.mark.django_db
def test_evergo_admin_load_customers_wizard_prefills_owned_profile_and_links_create(admin_client):
    """Regression: wizard should prefill the current user's profile and include profile-create link."""
    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="owned-profile@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.get(wizard_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert f'value="{profile.pk}" selected' in content
    assert reverse("admin:evergo_evergouser_add") in content
    assert 'name="next_view"' in content
    assert '<option value="orders" selected>Orders</option>' in content
    assert 'class="button">Cancel</a>' in content


@pytest.mark.django_db
def test_evergo_admin_load_customers_wizard_rejects_unowned_profile(monkeypatch, admin_client):
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

    called = {"value": False}

    def _fake_loader(self, *, raw_queries, timeout=20):
        called["value"] = True
        return {}

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", _fake_loader)

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.post(
        wizard_url,
        {"profile": other_profile.pk, "raw_queries": "J00830"},
    )

    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    assert called["value"] is False


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_admin_load_customers_wizard_load_all_submits_without_queries(
    monkeypatch, admin_client
):
    """Load-all mode should allow an empty query payload."""

    called = {"value": False}

    def _fake_loader(self, *, raw_queries, timeout=20):
        called["value"] = True
        assert raw_queries == ""
        return {
            "customers_loaded": 2,
            "orders_created": 2,
            "orders_updated": 0,
            "placeholders_created": 0,
            "unresolved": [],
            "loaded_customer_ids": [],
            "loaded_order_ids": [],
        }

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", _fake_loader)
    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="wildcard-admin@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.post(
        wizard_url,
        {"profile": profile.pk, "raw_queries": "", "load_mode": "all"},
    )

    assert response.status_code == 302
    assert called["value"] is True


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_admin_load_customers_wizard_requires_queries_for_filtered_mode(admin_client):
    """Filtered mode should require at least one query token."""

    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="wildcard-validation@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.post(
        wizard_url,
        {"profile": profile.pk, "raw_queries": "", "load_mode": "filtered"},
    )

    assert response.status_code == 200
    assert b"Enter at least one SO number or customer name." in response.content


@pytest.mark.django_db
def test_evergo_admin_change_action_runs_test_login_sync(monkeypatch, admin_client):
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

    called = {"value": False}

    def _fake_test_login(self, *, timeout=15):
        called["value"] = True
        return None

    monkeypatch.setattr(EvergoUser, "test_login", _fake_test_login)

    action_url = reverse(
        "admin:evergo_evergouser_actions",
        args=[profile.pk, "test_login_and_sync_action"],
    )
    response = admin_client.post(action_url, follow=True)

    assert response.status_code == 200
    assert called["value"] is True


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
