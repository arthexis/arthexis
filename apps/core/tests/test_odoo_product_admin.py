from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

import pytest

from apps.odoo.models import OdooEmployee, OdooProduct


@pytest.mark.django_db
def test_search_orders_for_selected_action_renders_matching_orders(
    admin_client, monkeypatch
):
    """The Odoo product action renders orders containing the selected Odoo products."""

    user = admin_client._force_user
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


@pytest.mark.django_db
def test_search_orders_for_selected_action_requires_odoo_link(admin_client):
    """The action shows an error when selected products have no linked Odoo IDs."""

    user = admin_client._force_user
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


@pytest.mark.django_db
def test_search_orders_view_accepts_post_selected_action(admin_client, monkeypatch):
    """The dedicated view accepts POSTed admin selections for preview compatibility."""

    user = admin_client._force_user
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
