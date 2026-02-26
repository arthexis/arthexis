"""Admin tests for the Evergo integration app."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoOrderFieldValue, EvergoUser


@pytest.mark.django_db
def test_evergo_admin_app_and_changelist_are_accessible(admin_client):
    """Ensure Evergo appears in admin and the model changelist renders."""

    app_url = reverse("admin:app_list", kwargs={"app_label": "evergo"})
    changelist_url = reverse("admin:evergo_evergouser_changelist")

    app_response = admin_client.get(app_url)
    changelist_response = admin_client.get(changelist_url)

    assert app_response.status_code == 200
    assert changelist_response.status_code == 200


@pytest.mark.django_db
def test_evergo_admin_load_customers_tool_action_is_registered_on_customers_only(admin_client):
    """Regression: Load Customers tool-action endpoint should exist for customers, not contractors."""

    customer_tool_url = reverse("admin:evergo_evergocustomer_actions", args=["load_customers_wizard"])
    response = admin_client.get(customer_tool_url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:evergo_evergocustomer_load_customers")

    contractor_tool_url = reverse("admin:evergo_evergouser_actions", args=["load_customers_wizard"])
    contractor_response = admin_client.get(contractor_tool_url)
    assert contractor_response.status_code == 404


@pytest.mark.django_db
def test_evergo_admin_load_orders_and_load_customers_actions_redirect_to_shared_wizard(admin_client):
    """Regression: both admin actions should redirect to the same shared wizard URL."""

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
def test_evergo_admin_change_form_renders_readonly_synced_fields(admin_client):
    """Ensure synced Evergo fields render in change form for profile inspection."""

    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-preview",
        email="suite-admin-preview@example.com",
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-admin-preview@example.com",
        evergo_password="secret",  # noqa: S106
        evergo_user_id=123,
        name="Suite Admin Preview",
        email="suite-admin-preview@example.com",
        empresa_id=99,
        subempresa_id=100,
    )

    change_url = reverse("admin:evergo_evergouser_change", args=[profile.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b"Evergo synced profile" in response.content
    assert b"Two-factor" in response.content


@pytest.mark.django_db
def test_evergo_admin_changelist_shows_evergo_email_instead_of_internal_ids(admin_client):
    """Ensure changelist prioritizes Evergo email over internal identifier columns."""

    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-listing",
        email="suite-admin-listing@example.com",
    )
    EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-listing@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    changelist_url = reverse("admin:evergo_evergouser_changelist")
    response = admin_client.get(changelist_url)

    assert response.status_code == 200

    content = response.content.lower()
    assert b"suite-listing@evergo.example.com" in content

    table_start = content.find(b"<table id=\"result_list\"")
    assert table_start != -1

    table_content = content[table_start:]
    thead_start = table_content.find(b"<thead")
    thead_end = table_content.find(b"</thead>")
    assert thead_start != -1 and thead_end != -1

    thead = table_content[thead_start:thead_end]
    assert b"evergo email" in thead
    assert b"evergo user id" not in thead
    assert b">empresa id<" not in thead
    assert b">subempresa id<" not in thead



@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.load_customers_from_queries")
def test_evergo_admin_load_customers_wizard_submits(mock_load_customers, admin_client):
    """Regression: customer load wizard should call profile sync method and redirect."""
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
    assert 'class="button">Cancel</a>' in content


@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.load_customers_from_queries")
def test_evergo_admin_load_customers_wizard_rejects_unowned_profile(mock_load_customers, admin_client):
    """Security regression: wizard should not allow selecting someone else's profile."""
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
    """Regression: change-form action should run login sync without requiring changelist selection."""

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
def test_evergo_order_and_field_value_admin_changelists_render(admin_client):
    """Ensure new order and field-value models are available in admin."""
    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-order",
        email="suite-admin-order@example.com",
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-order@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoOrder.objects.create(user=profile, remote_id=1, order_number="GLY0001")
    EvergoOrderFieldValue.objects.create(field_name="estatus", remote_id=1, remote_name="Nueva")

    order_changelist = admin_client.get(reverse("admin:evergo_evergoorder_changelist"))
    field_value_changelist = admin_client.get(reverse("admin:evergo_evergoorderfieldvalue_changelist"))
    customer_changelist = admin_client.get(reverse("admin:evergo_evergocustomer_changelist"))

    assert order_changelist.status_code == 200
    assert field_value_changelist.status_code == 200
    assert customer_changelist.status_code == 200


@pytest.mark.django_db
def test_evergo_customer_admin_change_form_shows_view_on_site_and_artifacts_inline(admin_client):
    """Regression: customer admin should expose View on site and artifact attachment inline."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-customer-public",
        email="suite-admin-customer-public@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-customer-public@example.com",
        evergo_password="secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="Public Jane", latest_so="SO-501")

    change_url = reverse("admin:evergo_evergocustomer_change", args=[customer.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    content = response.content.decode().lower()
    assert "view on site" in content
    assert "artifacts-0-file" in content


@pytest.mark.django_db
def test_evergo_customer_export_view_renders_selectable_columns_and_tsv(admin_client):
    """Regression: export view should offer selectable columns and TSV output."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-export-columns",
        email="suite-admin-export-columns@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-export-columns@example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoCustomer.objects.create(
        user=profile,
        remote_id=7001,
        name="Export User",
        email="export-user@example.com",
    )

    export_url = reverse("admin:evergo_evergocustomer_export")
    response = admin_client.get(export_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'type="checkbox" name="export_columns" value="remote_id" checked' in content
    assert "Import ID" in content
    assert '<option value="tsv">TSV</option>' in content


@pytest.mark.django_db
def test_evergo_customer_export_view_honors_column_selection_for_tsv(admin_client):
    """Regression: TSV export should include only selected columns in requested order."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-export-tsv",
        email="suite-admin-export-tsv@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-export-tsv@example.com",
        evergo_password="secret",  # noqa: S106
    )
    EvergoCustomer.objects.create(
        user=profile,
        remote_id=7002,
        name="TSV Export User",
        email="tsv-export@example.com",
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
    assert rows[0] == "remote_id	name"
    assert rows[1].startswith("7002	TSV Export User")
