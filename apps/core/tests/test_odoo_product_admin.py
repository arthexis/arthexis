from __future__ import annotations

from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

import pytest

from apps.features.models import Feature
from apps.odoo.models import OdooEmployee, OdooProduct
from apps.odoo.sync_features import ODOO_CRM_SYNC_SUITE_FEATURE_SLUG
from apps.users.models import User


def _odoo_sync_metadata(*, employee_import: str = "enabled") -> dict[str, dict[str, str]]:
    """Build Odoo CRM sync metadata parameters for tests."""

    return {
        "parameters": {
            "deployment_discovery": "enabled",
            "employee_import": employee_import,
            "evergo_users": "enabled",
        }
    }

@pytest.mark.django_db
def test_search_orders_for_selected_action_renders_matching_orders(
    admin_client, admin_user, monkeypatch
):
    """The Odoo product action renders orders containing the selected Odoo products."""

    user = admin_user
    OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    product = OdooProduct.objects.create(
        name="Arthexis Product",
        description="Synced from Odoo",
        renewal_period=30,
        odoo_product={"id": 10, "name": "Odoo Product"},
    )

    def fake_execute(self, model, method, *args, **kwargs):
        if model == "sale.order.line" and method == "search_read":
            return [
                {
                    "order_id": [1001, "S0001"],
                    "product_id": [10, "Odoo Product"],
                    "name": "Odoo line",
                    "product_uom_qty": 2,
                    "price_total": 150,
                }
            ]
        if model == "sale.order" and method == "search_read":
            return [
                {
                    "id": 1001,
                    "name": "S0001",
                    "partner_id": [501, "Acme"],
                    "state": "sale",
                    "date_order": "2025-01-01 10:00:00",
                    "amount_total": 150,
                }
            ]
        raise AssertionError(f"Unexpected call: {model}.{method}")

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    response = admin_client.post(
        reverse("admin:odoo_odooproduct_changelist"),
        {
            "action": "search_orders_for_selected",
            "_selected_action": [str(product.pk)],
            "index": "0",
        },
    )

    assert response.status_code == 302
    follow_response = admin_client.get(response.url)
    assert follow_response.status_code == 200
    assert "S0001" in follow_response.rendered_content
    assert "Acme" in follow_response.rendered_content
    assert "Odoo line" in follow_response.rendered_content


@pytest.mark.integration
@pytest.mark.django_db
def test_search_orders_for_selected_action_requires_odoo_link(admin_client, admin_user):
    """The action shows an error when selected products have no linked Odoo IDs."""

    user = admin_user
    OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    product = OdooProduct.objects.create(
        name="Local Product",
        description="No odoo link",
        renewal_period=30,
        odoo_product={},
    )

    response = admin_client.post(
        reverse("admin:odoo_odooproduct_changelist"),
        {
            "action": "search_orders_for_selected",
            "_selected_action": [str(product.pk)],
            "index": "0",
        },
    )

    assert response.status_code == 302
    follow_response = admin_client.get(response.url)
    assert follow_response.status_code == 200
    assert (
        "None of the selected products are linked to an Odoo product ID."
        in follow_response.rendered_content
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_search_orders_view_accepts_post_selected_action(admin_client, admin_user, monkeypatch):
    """The dedicated view accepts POSTed admin selections for preview compatibility."""

    user = admin_user
    OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    product = OdooProduct.objects.create(
        name="Arthexis Product",
        description="Synced from Odoo",
        renewal_period=30,
        odoo_product={"id": 10, "name": "Odoo Product"},
    )

    def fake_execute(self, model, method, *args, **kwargs):
        if model == "sale.order.line" and method == "search_read":
            return [
                {
                    "order_id": [1001, "S0001"],
                    "product_id": [10, "Odoo Product"],
                    "name": "Odoo line",
                    "product_uom_qty": 2,
                    "price_total": 150,
                }
            ]
        if model == "sale.order" and method == "search_read":
            return [
                {
                    "id": 1001,
                    "name": "S0001",
                    "partner_id": [501, "Acme"],
                    "state": "sale",
                    "date_order": "2025-01-01 10:00:00",
                    "amount_total": 150,
                }
            ]
        raise AssertionError(f"Unexpected call: {model}.{method}")

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    response = admin_client.post(
        reverse("admin:odoo_odooproduct_search_orders_for_selected"),
        {"_selected_action": [str(product.pk)]},
    )

    assert response.status_code == 200
    assert "S0001" in response.rendered_content


@pytest.mark.integration
@pytest.mark.django_db
def test_load_employees_changelist_action_posts_to_import_endpoint(admin_client, admin_user):
    """The changelist object action endpoint redirects to the import view."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    response = admin_client.post(
        reverse("admin:odoo_odooemployee_actions", kwargs={"tool": "load_employees"})
    )

    assert response.status_code == 302
    assert response.url == reverse("admin:odoo_odooemployee_load_employees")

@pytest.mark.integration
@pytest.mark.django_db
def test_load_employees_action_creates_missing_odoo_profiles(admin_client, admin_user, monkeypatch):
    """The Odoo employee tool action creates missing local users and profiles."""

    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    OdooEmployee.objects.create(
        user=User.objects.create(username="existing-user"),
        host=profile.host,
        database=profile.database,
        username="existing",
        password="secret",
        odoo_uid=10,
        verified_on=timezone.now(),
    )

    def fake_execute(self, model, method, *args, **kwargs):
        assert self.pk == profile.pk
        assert model == "res.users"
        assert method == "search_read"
        return [
            {
                "id": 10,
                "name": "Existing User",
                "email": "existing@example.com",
                "login": "existing",
                "partner_id": [201, "Existing"],
            },
            {
                "id": 11,
                "name": "Ana Gomez",
                "email": "ana@example.com",
                "login": "ana",
                "partner_id": [202, "Ana"],
            },
        ]

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    response = admin_client.post(reverse("admin:odoo_odooemployee_load_employees"))
    assert response.status_code == 302

    created_profile = OdooEmployee.objects.get(odoo_uid=11)
    assert created_profile.username == "ana"
    assert created_profile.host == profile.host
    assert created_profile.database == profile.database
    assert created_profile.user.username == "ana"
    assert created_profile.user.has_usable_password() is False
    assert created_profile.password == ""
    assert created_profile.verified_on is None
    assert OdooEmployee.objects.filter(host=profile.host, database=profile.database).count() == 3


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("disable_suite", [True, False])
def test_load_employees_action_respects_sync_feature_toggle(
    admin_client,
    admin_user,
    monkeypatch,
    disable_suite,
):
    """Employee import should be skipped when an Odoo sync feature toggle is off."""

    Feature.objects.update_or_create(
        slug=ODOO_CRM_SYNC_SUITE_FEATURE_SLUG,
        defaults={
            "display": "Odoo CRM Sync",
            "is_enabled": not disable_suite,
            "metadata": _odoo_sync_metadata(
                employee_import="enabled" if disable_suite else "disabled"
            ),
        },
    )

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("execute should not be called when feature is disabled")

    monkeypatch.setattr(OdooEmployee, "execute", fail_execute)

    response = admin_client.post(reverse("admin:odoo_odooemployee_load_employees"))
    assert response.status_code == 302
    assert OdooEmployee.objects.count() == 1

@pytest.mark.integration
@pytest.mark.django_db
def test_load_employees_action_requires_verified_profile(admin_client, admin_user, monkeypatch):
    """The tool action redirects without syncing when Odoo credentials are not verified."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("execute should not be called for unverified profiles")

    monkeypatch.setattr(OdooEmployee, "execute", fail_execute)

    response = admin_client.post(reverse("admin:odoo_odooemployee_load_employees"))
    assert response.status_code == 302
    assert OdooEmployee.objects.count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_load_employees_action_rejects_get_requests(admin_client, admin_user, monkeypatch):
    """The import endpoint only performs sync when called with POST."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("execute should not be called for GET requests")

    monkeypatch.setattr(OdooEmployee, "execute", fail_execute)

    response = admin_client.get(reverse("admin:odoo_odooemployee_load_employees"))
    assert response.status_code == 302
    assert OdooEmployee.objects.count() == 1


@pytest.mark.django_db
def test_load_employees_action_requires_change_permission(client, monkeypatch):
    """Users without change permission cannot trigger the import endpoint."""

    viewer = User.objects.create_user(
        username="viewer",
        password="viewer-pass",
        is_staff=True,
    )
    viewer.user_permissions.add(Permission.objects.get(codename="view_odooemployee"))

    OdooEmployee.objects.create(
        user=viewer,
        host="https://odoo.example.com",
        database="odoodb",
        username="viewer",
        password="secret",
        odoo_uid=101,
        verified_on=timezone.now(),
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("execute should not be called without change permission")

    monkeypatch.setattr(OdooEmployee, "execute", fail_execute)

    assert client.login(username="viewer", password="viewer-pass")
    response = client.post(reverse("admin:odoo_odooemployee_load_employees"))
    assert response.status_code == 403
