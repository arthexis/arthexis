"""Admin tests for the Evergo integration app."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoOrderFieldValue, EvergoUser


@pytest.fixture
def evergo_customer_export_record(db):
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
def test_evergo_customer_export_view_renders_selectable_columns_and_tsv(
    admin_client, evergo_customer_export_record
):
    """Regression: export view should offer selectable columns and TSV output."""

    evergo_customer_export_record(
        username="suite-admin-export-columns",
        email="suite-admin-export-columns@example.com",
        remote_id=7001,
        name="Export User",
    )

    export_url = reverse("admin:evergo_evergocustomer_export")
    response = admin_client.get(export_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'type="checkbox" name="export_columns" value="remote_id" checked' in content
    assert "Import ID" in content
    assert '<option value="tsv">TSV</option>' in content


@pytest.mark.django_db
def test_evergo_customer_export_view_honors_column_selection_for_tsv(
    admin_client, evergo_customer_export_record
):
    """Regression: TSV export should include only selected columns in requested order."""

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
    assert rows[0] == "remote_id	name"
    assert rows[1].startswith("7002	TSV Export User")


@pytest.mark.django_db
def test_evergo_customer_export_view_rejects_empty_column_selection(
    admin_client, evergo_customer_export_record
):
    """Regression: exporting with no selected columns should fail fast."""

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
def test_evergo_customer_admin_changelist_shows_status_and_clean_phone(admin_client):
    """Regression: changelist should show last SO status and trim +52/52 phone prefixes."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-customer-list",
        email="suite-admin-customer-list@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-customer-list@example.com",
        evergo_password="secret",  # noqa: S106
    )
    order = EvergoOrder.objects.create(
        user=profile,
        remote_id=7001,
        order_number="SO-7001",
        status_name="Instalado",
    )
    EvergoCustomer.objects.create(
        user=profile,
        name="Customer List",
        latest_so="SO-7001",
        latest_order=order,
        phone_number="+52 5512345678",
    )
    EvergoCustomer.objects.create(
        user=profile,
        name="Customer List 2",
        latest_so="SO-7001-B",
        latest_order=order,
        phone_number="52 5598765432",
    )

    changelist_url = reverse("admin:evergo_evergocustomer_changelist")
    response = admin_client.get(changelist_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Status of Last SO" in content
    assert "Instalado" in content
    assert "+52 5512345678" not in content
    assert "5512345678" in content
    assert "52 5598765432" not in content
    assert "5598765432" in content


@pytest.mark.django_db
def test_evergo_customer_admin_changelist_links_and_filters(admin_client):
    """Regression: customer changelist should link customer and SO columns and filter by locality/status."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-customer-links",
        email="suite-admin-customer-links@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-customer-links@example.com",
        evergo_password="secret",  # noqa: S106
    )
    installed_order = EvergoOrder.objects.create(user=profile, remote_id=9101, status_name="Instalado")
    scheduled_order = EvergoOrder.objects.create(user=profile, remote_id=9102, status_name="Programado")

    EvergoCustomer.objects.create(
        user=profile,
        name="Customer Apodaca",
        latest_so="SO-9101",
        latest_order=installed_order,
        address="santa barbara 404 Apodaca 66647",
        raw_payload={"orden_instalacion": {"municipio": "Apodaca", "ciudad": "Ciudad Apodaca"}},
    )
    EvergoCustomer.objects.create(
        user=profile,
        name="Customer Monterrey",
        latest_so="SO-9102",
        latest_order=scheduled_order,
        address="centro Monterrey 64000",
        raw_payload={"orden_instalacion": {"ciudad": "Monterrey"}},
    )

    changelist_url = reverse("admin:evergo_evergocustomer_changelist")
    response = admin_client.get(changelist_url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    assert 'scope="col" class="column-user"' not in content
    assert '/admin/evergo/evergocustomer/' in content
    assert reverse("admin:evergo_evergoorder_change", args=[installed_order.pk]) in content
    assert "SO-9101" in content

    city_filtered = admin_client.get(changelist_url, {"city_municipio": "Apodaca"})
    assert city_filtered.status_code == 200
    city_content = city_filtered.content.decode("utf-8")
    assert "Customer Apodaca" in city_content
    assert "Customer Monterrey" not in city_content

    status_filtered = admin_client.get(changelist_url, {"last_so_status": "Programado"})
    assert status_filtered.status_code == 200
    status_content = status_filtered.content.decode("utf-8")
    assert "Customer Monterrey" in status_content
    assert "Customer Apodaca" not in status_content

def test_evergo_customer_admin_change_form_has_status_readonly(admin_client):
    """Regression: customer change form should include readonly status of last SO."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-customer-status",
        email="suite-admin-customer-status@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-customer-status@example.com",
        evergo_password="secret",  # noqa: S106
    )
    order = EvergoOrder.objects.create(user=profile, remote_id=7002, status_name="Programado")
    customer = EvergoCustomer.objects.create(user=profile, name="Customer Status", latest_order=order)

    change_url = reverse("admin:evergo_evergocustomer_change", args=[customer.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Status of Last SO" in content
    assert "Programado" in content


@pytest.mark.django_db
def test_evergo_customer_admin_date_filters_local_and_remote(admin_client):
    """Regression: customer changelist should filter local and remote date ranges."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-customer-filters",
        email="suite-admin-customer-filters@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-customer-filters@example.com",
        evergo_password="secret",  # noqa: S106
    )

    now = timezone.now()
    order_recent = EvergoOrder.objects.create(
        user=profile,
        remote_id=8001,
        source_updated_at=now,
    )
    order_old = EvergoOrder.objects.create(
        user=profile,
        remote_id=8002,
        source_updated_at=now - timedelta(days=40),
    )

    recent = EvergoCustomer.objects.create(
        user=profile,
        name="Recent Customer",
        latest_order=order_recent,
        latest_order_updated_at=now,
    )
    old = EvergoCustomer.objects.create(
        user=profile,
        name="Old Customer",
        latest_order=order_old,
        latest_order_updated_at=now - timedelta(days=40),
    )

    EvergoCustomer.objects.filter(pk=old.pk).update(
        created_at=now - timedelta(days=40),
        refreshed_at=now - timedelta(days=40),
    )

    changelist_url = reverse("admin:evergo_evergocustomer_changelist")

    for range_value in ("today", "week", "month"):
        local_loaded_response = admin_client.get(changelist_url, {"loaded_at_range": range_value})
        assert local_loaded_response.status_code == 200
        local_loaded_content = local_loaded_response.content.decode("utf-8")
        assert recent.name in local_loaded_content
        assert old.name not in local_loaded_content

        local_updated_response = admin_client.get(changelist_url, {"updated_at_range": range_value})
        assert local_updated_response.status_code == 200
        local_updated_content = local_updated_response.content.decode("utf-8")
        assert recent.name in local_updated_content
        assert old.name not in local_updated_content

        remote_updated_response = admin_client.get(changelist_url, {"remote_updated_at_range": range_value})
        assert remote_updated_response.status_code == 200
        remote_updated_content = remote_updated_response.content.decode("utf-8")
        assert recent.name in remote_updated_content
        assert old.name not in remote_updated_content
