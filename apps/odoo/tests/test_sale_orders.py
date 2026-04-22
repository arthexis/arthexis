from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.odoo.models import (
    OdooEmployee,
    OdooSaleFactor,
    OdooSaleFactorProductRule,
    OdooSaleOrderTemplate,
)
from apps.odoo.sale_orders import OdooSaleOrderBuilder


@pytest.mark.django_db
def test_create_order_combines_template_and_factor_lines(monkeypatch):
    user = get_user_model().objects.create_user(username="agent")
    profile = OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoo",
        username="user",
        password="secret",
        odoo_uid=12,
    )
    salesperson = OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoo",
        username="sales",
        password="secret",
        odoo_uid=77,
    )

    template = OdooSaleOrderTemplate.objects.create(
        name="Base SaaS",
        odoo_template={"id": 99, "name": "Base"},
        note_template="Welcome [USER.username]",
        resolve_note_sigils=False,
        salesperson=salesperson,
    )
    factor = OdooSaleFactor.objects.create(name="Seats", code="seats")
    rule = OdooSaleFactorProductRule.objects.create(
        factor=factor,
        name="Extra seat",
        odoo_product={"id": 501, "name": "Seat"},
        quantity_mode=OdooSaleFactorProductRule.QuantityMode.FACTOR_LINEAR,
        factor_multiplier=Decimal("2"),
    )
    rule.refresh_from_db()

    captured: list[tuple[str, str, tuple, dict]] = []

    def _execute(model, method, *args, **kwargs):
        captured.append((model, method, args, kwargs))
        if model == "sale.order.template.line" and method == "search_read":
            return [
                {
                    "name": "Base plan",
                    "product_id": [300, "Base"],
                    "product_uom_qty": 1,
                    "price_unit": 10,
                }
            ]
        if model == "res.partner" and method == "create":
            return 456
        if model == "sale.order" and method == "create":
            return 789
        raise AssertionError(f"Unexpected RPC call: {model}.{method}")

    monkeypatch.setattr(profile, "execute", _execute)

    result = OdooSaleOrderBuilder(profile=profile).create_order(
        template=template,
        customer_name="Acme",
        customer_email="sales@acme.test",
        factor_values={"seats": Decimal("3")},
    )

    assert result.order_id == 789
    assert result.customer_id == 456
    assert result.order_url == (
        "https://odoo.example.com/web#id=789&model=sale.order&view_type=form"
    )

    order_create = next(call for call in captured if call[0] == "sale.order")
    payload = order_create[2][0][0]
    assert payload["user_id"] == 77
    assert payload["partner_id"] == 456
    assert payload["sale_order_template_id"] == 99
    assert payload["note"] == "Welcome [USER.username]"
    assert payload["order_line"][0][2]["product_id"] == 300
    assert payload["order_line"][1][2]["product_id"] == 501
    assert payload["order_line"][1][2]["product_uom_qty"] == 6.0


@pytest.mark.django_db
def test_factor_with_template_restrictions_applies_only_to_selected_template(monkeypatch):
    user = get_user_model().objects.create_user(username="agent-2")
    profile = OdooEmployee.objects.create(
        user=user,
        host="https://odoo.example.com",
        database="odoo",
        username="user",
        password="secret",
        odoo_uid=13,
    )

    allowed_template = OdooSaleOrderTemplate.objects.create(
        name="Allowed",
        odoo_template={"id": 10, "name": "Allowed"},
    )
    blocked_template = OdooSaleOrderTemplate.objects.create(
        name="Blocked",
        odoo_template={"id": 11, "name": "Blocked"},
    )

    factor = OdooSaleFactor.objects.create(name="Priority", code="priority")
    factor.templates.add(allowed_template)
    OdooSaleFactorProductRule.objects.create(
        factor=factor,
        name="Priority processing",
        odoo_product={"id": 700, "name": "Priority"},
        fixed_quantity=Decimal("1"),
    )

    def _execute(_model, _method, *args, **kwargs):
        if _model == "sale.order.template.line":
            return []
        if _model == "res.partner":
            return 1
        if _model == "sale.order":
            return args[0][0]["order_line"]
        return []

    monkeypatch.setattr(profile, "execute", _execute)
    builder = OdooSaleOrderBuilder(profile=profile)

    allowed_lines = builder.create_order(
        template=allowed_template,
        customer_name="Allowed",
        customer_email="allowed@test",
        factor_values={"priority": Decimal("1")},
    ).order_id

    blocked_lines = builder.create_order(
        template=blocked_template,
        customer_name="Blocked",
        customer_email="blocked@test",
        factor_values={"priority": Decimal("1")},
    ).order_id

    assert len(allowed_lines) == 1
    assert allowed_lines[0][2]["product_id"] == 700
    assert blocked_lines == []
