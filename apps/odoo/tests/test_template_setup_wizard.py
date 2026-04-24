from __future__ import annotations

import pytest
from django.contrib import admin
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.odoo.admin import OdooSaleOrderTemplateAdmin
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

    def execute(model, method, *args, **kwargs):
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
        lambda self, model, method, *args, **kwargs: execute(
            model, method, *args, **kwargs
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


@pytest.mark.django_db
def test_setup_templates_step_two_generates_unique_factor_code(admin_client):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    product = OdooProduct.objects.create(
        name="Addon",
        renewal_period=30,
        odoo_product={"id": 501, "name": "Addon"},
    )

    payload = {
        "name_prefix": "Setup",
        "templates": [str(source_template.pk)],
        "products": [str(product.pk)],
        "employees": [],
    }

    first_response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        payload,
    )
    second_response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        payload,
    )

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    assert OdooSaleFactor.objects.filter(code="setup-products").exists()
    assert OdooSaleFactor.objects.filter(code="setup-products-2").exists()


@pytest.mark.django_db
def test_setup_templates_step_one_employee_update_syncs_user(
    admin_client,
    admin_user,
    django_user_model,
    monkeypatch,
):
    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="profile-admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )

    linked_user = django_user_model.objects.create_user(
        username="legacy-login",
        password="secret",
        email="legacy@example.com",
    )
    OdooEmployee.objects.create(
        user=linked_user,
        host=profile.host,
        database=profile.database,
        username="legacy-login",
        password="",
        odoo_uid=55,
        verified_on=timezone.now(),
    )

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        domain = args[0] if args else kwargs.get("domain")
        if model == "res.users":
            if domain == [[("active", "=", True), ("share", "=", False)]]:
                return [
                    {
                        "id": 55,
                        "name": "Updated User",
                        "email": "new@example.com",
                        "login": "new-login",
                        "partner_id": [301, "Partner"],
                    }
                ]
            return [
                {
                    "id": 55,
                    "name": "Updated User",
                    "email": "new@example.com",
                    "login": "new-login",
                    "partner_id": [301, "Partner"],
                }
            ]
        raise AssertionError(f"Unexpected model: {model}")

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(
            model, method, *args, **kwargs
        ),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "employees",
            "selected_ids": ["55"],
        },
    )

    assert response.status_code == 302
    updated_employee = OdooEmployee.objects.get(
        odoo_uid=55,
        host=profile.host,
        database=profile.database,
    )
    updated_user = updated_employee.user
    assert updated_user is not None
    assert updated_user.username.startswith("new-login")
    assert updated_user.username != "legacy-login"
    assert updated_user.email == "new@example.com"
    assert updated_employee.username == "legacy-login"
    assert updated_employee.email == "new@example.com"
    assert updated_employee.partner_id == 301


@pytest.mark.django_db
def test_setup_templates_step_one_employee_import_truncates_long_username(
    admin_client,
    admin_user,
    django_user_model,
    monkeypatch,
):
    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="profile-admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    username_field = django_user_model._meta.get_field(django_user_model.USERNAME_FIELD)
    long_login = "x" * (username_field.max_length + 25)

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        if model != "res.users":
            raise AssertionError(f"Unexpected model: {model}")
        domain = args[0] if args else kwargs.get("domain")
        if domain == [[("active", "=", True), ("share", "=", False)]]:
            return [
                {
                    "id": 75,
                    "name": "Long Username",
                    "email": "long@example.com",
                    "login": long_login,
                    "partner_id": [401, "Partner"],
                }
            ]
        return [
            {
                "id": 75,
                "name": "Long Username",
                "email": "long@example.com",
                "login": long_login,
                "partner_id": [401, "Partner"],
            }
        ]

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "employees",
            "selected_ids": ["75"],
        },
    )

    assert response.status_code == 302
    employee = OdooEmployee.objects.get(odoo_uid=75)
    assert len(employee.user.username) <= username_field.max_length  # type: ignore[union-attr]


@pytest.mark.django_db
def test_import_employee_rolls_back_user_when_employee_create_fails(
    admin_user, django_user_model, monkeypatch
):
    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="profile-admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    model_admin = OdooSaleOrderTemplateAdmin(OdooSaleOrderTemplate, admin.site)

    original_create = OdooEmployee.objects.create

    def create_employee(*args, **kwargs):
        if kwargs.get("odoo_uid") == 99:
            raise IntegrityError("forced failure")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(OdooEmployee.objects, "create", create_employee)

    with pytest.raises(IntegrityError):
        model_admin._import_employee(
            profile,
            {
                "id": 99,
                "name": "Fail Employee",
                "email": "fail@example.com",
                "login": "rollback-test",
                "partner_id": [999, "Partner"],
            },
        )

    assert not django_user_model.objects.filter(username="rollback-test").exists()
