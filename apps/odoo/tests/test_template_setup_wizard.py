from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.odoo.models import (
    OdooEmployee,
    OdooProduct,
    OdooSaleFactor,
    OdooSaleFactorProductRule,
    OdooSaleOrderTemplate,
)


@pytest.mark.django_db
def test_setup_templates_step_one_imports_selected_records(admin_client, admin_user, monkeypatch):
    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )

    def execute(model, method, domain, **kwargs):
        assert method == "search_read"
        if model == "sale.order.template":
            fields = kwargs.get("fields") or []
            if fields == ["id", "name"]:
                return [{"id": 100, "name": "Starter Quote"}]
            return [{"id": 100, "name": "Starter Quote", "note": "Generated"}]
        raise AssertionError(f"Unexpected model: {model}")

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, domain, **kwargs: execute(
            model, method, domain, **kwargs
        ),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "templates",
            "selected_ids": ["100"],
        },
    )

    assert response.status_code == 302
    created = OdooSaleOrderTemplate.objects.get(odoo_template__id=100)
    assert created.name == "Starter Quote"


@pytest.mark.django_db
def test_setup_templates_step_two_creates_linked_objects(admin_client):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    product = OdooProduct.objects.create(
        name="Addon",
        renewal_period=30,
        odoo_product={"id": 501, "name": "Addon"},
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        {
            "name_prefix": "Setup",
            "templates": [str(source_template.pk)],
            "products": [str(product.pk)],
            "employees": [],
        },
    )

    assert response.status_code == 302
    assert OdooSaleOrderTemplate.objects.filter(name="Setup: Base").exists()
    factor = OdooSaleFactor.objects.get(name="Setup Products")
    linked_template = OdooSaleOrderTemplate.objects.get(name="Setup: Base")
    assert factor.templates.filter(pk=linked_template.pk).exists()
    assert OdooSaleFactorProductRule.objects.filter(
        factor=factor,
        odoo_product__id=501,
    ).exists()
