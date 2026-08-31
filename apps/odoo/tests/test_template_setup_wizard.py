from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.odoo import admin as odoo_admin
from apps.odoo.admin import OdooSaleOrderTemplateAdmin
from apps.odoo.models import (
    OdooEmployee,
    OdooProduct,
    OdooSaleFactor,
    OdooSaleFactorProductRule,
    OdooSaleOrderTemplate,
)


def _grant_model_perms(user, model):
    opts = model._meta
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label=opts.app_label,
            codename=f"add_{opts.model_name}",
        ),
        Permission.objects.get(
            content_type__app_label=opts.app_label,
            codename=f"change_{opts.model_name}",
        ),
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
    assert created.odoo_template["host"] == profile.host
    assert created.odoo_template["database"] == profile.database


@pytest.mark.django_db
def test_setup_templates_step_one_scopes_template_upsert_by_odoo_instance(
    admin_client, admin_user, monkeypatch
):
    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.two.example.com",
        database="odoo-two",
        username="admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    OdooSaleOrderTemplate.objects.create(
        name="Original Other Instance",
        odoo_template={
            "id": 100,
            "name": "Starter Quote",
            "host": "https://odoo.one.example.com",
            "database": "odoo-one",
        },
    )
    partially_scoped = OdooSaleOrderTemplate.objects.create(
        name="Partially Scoped Other Instance",
        odoo_template={
            "id": 100,
            "name": "Starter Quote",
            "host": "https://odoo.one.example.com",
        },
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
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "templates",
            "selected_ids": ["100"],
        },
    )

    assert response.status_code == 302
    assert OdooSaleOrderTemplate.objects.filter(odoo_template__id=100).count() == 3
    assert OdooSaleOrderTemplate.objects.filter(
        odoo_template__id=100,
        odoo_template__host=profile.host,
        odoo_template__database=profile.database,
    ).exists()
    partially_scoped.refresh_from_db()
    assert partially_scoped.name == "Partially Scoped Other Instance"
    assert partially_scoped.odoo_template["host"] == "https://odoo.one.example.com"
    assert "database" not in partially_scoped.odoo_template


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
def test_setup_templates_step_two_assigns_one_salesperson(
    admin_client, admin_user
):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    salesperson = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="salesperson",
        password="secret",
        odoo_uid=2,
        verified_on=timezone.now(),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        {
            "name_prefix": "Setup",
            "templates": [str(source_template.pk)],
            "products": [],
            "employees": str(salesperson.pk),
        },
    )

    assert response.status_code == 302
    copied = OdooSaleOrderTemplate.objects.get(name="Setup: Base")
    assert copied.salesperson == salesperson


@pytest.mark.django_db
def test_setup_templates_step_two_rejects_multiple_salespeople(
    admin_client, admin_user, django_user_model
):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    first = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="first-salesperson",
        password="secret",
        odoo_uid=2,
        verified_on=timezone.now(),
    )
    second_user = django_user_model.objects.create_user(username="second-salesperson")
    second = OdooEmployee.objects.create(
        user=second_user,
        host="https://odoo.example.com",
        database="odoo",
        username="second-salesperson",
        password="secret",
        odoo_uid=3,
        verified_on=timezone.now(),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        {
            "name_prefix": "Setup",
            "templates": [str(source_template.pk)],
            "products": [],
            "employees": [str(first.pk), str(second.pk)],
        },
    )

    assert response.status_code == 200
    assert OdooSaleOrderTemplate.objects.filter(name="Setup: Base").count() == 0


@pytest.mark.django_db
def test_setup_templates_step_one_truncates_imported_product_name(
    admin_client, admin_user, monkeypatch
):
    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    product_name_max = OdooProduct._meta.get_field("name").max_length or 100
    long_name = "P" * (product_name_max + 20)

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        if model != "product.product":
            raise AssertionError(f"Unexpected model: {model}")
        fields = kwargs.get("fields") or []
        if fields == ["id", "name"]:
            return [{"id": 701, "name": long_name}]
        return [{"id": 701, "name": long_name, "description_sale": "Remote description"}]

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "products",
            "selected_ids": ["701"],
        },
    )

    assert response.status_code == 302
    created = OdooProduct.objects.get(odoo_product__id=701)
    assert len(created.name) == product_name_max
    assert created.odoo_product["host"] == "https://odoo.example.com"


@pytest.mark.django_db
def test_setup_templates_step_one_preserves_product_renewal_period_on_reimport(
    admin_client, admin_user, monkeypatch
):
    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    product = OdooProduct.objects.create(
        name="Local Addon",
        description="Old description",
        renewal_period=90,
        odoo_product={
            "id": 701,
            "name": "Local Addon",
            "host": "https://odoo.example.com",
            "database": "odoo",
        },
    )

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        if model != "product.product":
            raise AssertionError(f"Unexpected model: {model}")
        fields = kwargs.get("fields") or []
        if fields == ["id", "name"]:
            return [{"id": 701, "name": "Remote Addon"}]
        return [{"id": 701, "name": "Remote Addon", "description_sale": "Updated"}]

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "products",
            "selected_ids": ["701"],
        },
    )

    assert response.status_code == 302
    product.refresh_from_db()
    assert product.name == "Remote Addon"
    assert product.description == "Updated"
    assert product.renewal_period == 90


@pytest.mark.django_db
def test_setup_templates_step_one_truncates_imported_template_name(
    admin_client, admin_user, monkeypatch
):
    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoo",
        username="admin",
        password="secret",
        odoo_uid=1,
        verified_on=timezone.now(),
    )
    template_name_max = OdooSaleOrderTemplate._meta.get_field("name").max_length or 255
    long_name = "T" * (template_name_max + 20)

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        if model != "sale.order.template":
            raise AssertionError(f"Unexpected model: {model}")
        fields = kwargs.get("fields") or []
        if fields == ["id", "name"]:
            return [{"id": 801, "name": long_name}]
        return [{"id": 801, "name": long_name, "note": "Remote note"}]

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "templates",
            "selected_ids": ["801"],
        },
    )

    assert response.status_code == 302
    created = OdooSaleOrderTemplate.objects.get(odoo_template__id=801)
    assert len(created.name) == template_name_max
    assert created.name == long_name[:template_name_max]


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
            assert domain in (
                [[("active", "=", True), ("share", "=", False)]],
                [[("id", "=", 55)]],
            )
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
    assert updated_user.username == "new-login"
    assert updated_user.email == "new@example.com"
    assert updated_employee.username == "new-login"
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
        assert domain in (
            [[("active", "=", True), ("share", "=", False)]],
            [[("id", "=", 75)]],
        )
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


@pytest.mark.django_db
def test_setup_templates_step_one_employee_import_requires_auth_user_permissions(
    client,
    django_user_model,
    monkeypatch,
):
    staff_user = django_user_model.objects.create_user(
        username="staff-importer",
        password="testpass",
        is_staff=True,
    )
    _grant_model_perms(staff_user, OdooSaleOrderTemplate)
    _grant_model_perms(staff_user, OdooEmployee)
    profile = OdooEmployee.objects.create(
        user=staff_user,
        host="https://odoo.example.com",
        database="odoo",
        username="staff-importer",
        password="secret",
        odoo_uid=1001,
        verified_on=timezone.now(),
    )
    client.force_login(staff_user)

    def execute(model, method, *args, **kwargs):
        assert method == "search_read"
        if model == "sale.order.template":
            return []
        if model == "res.users":
            return [{"id": 88, "name": "Employee No Access", "login": "emp-no-access"}]
        raise AssertionError(f"Unexpected model: {model}")

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )

    response = client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {"source_type": "employees", "selected_ids": ["88"]},
    )

    response_messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any(
        "do not have permission to synchronize authentication users" in message
        for message in response_messages
    )
    assert (
        OdooEmployee.objects.filter(host=profile.host, database=profile.database, odoo_uid=88).count()
        == 0
    )


@pytest.mark.django_db
def test_setup_templates_step_one_reports_database_write_failures(
    admin_client, admin_user, monkeypatch
):
    OdooEmployee.objects.create(
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
        if model != "product.product":
            raise AssertionError(f"Unexpected model: {model}")
        return [{"id": 701, "name": "Remote Addon"}]

    def fail_import(*args, **kwargs):
        raise IntegrityError("forced write failure")

    monkeypatch.setattr(
        OdooEmployee,
        "execute",
        lambda self, model, method, *args, **kwargs: execute(model, method, *args, **kwargs),
    )
    monkeypatch.setattr(OdooSaleOrderTemplateAdmin, "_import_source_selection", fail_import)

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates"),
        {
            "source_type": "products",
            "selected_ids": ["701"],
        },
    )

    assert response.status_code == 302
    assert response.url.endswith("?source_type=products")
    response = admin_client.get(response.url)
    response_messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Import failed while saving local records" in message for message in response_messages)


@pytest.mark.django_db
def test_setup_templates_step_two_rejects_overlong_cloned_template_name(admin_client):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="T" * 200,
        odoo_template={"id": 90, "name": "Base"},
    )

    response = admin_client.post(
        reverse("admin:odoo_odoosaleordertemplate_setup_templates_create"),
        {
            "name_prefix": "P" * 120,
            "templates": [str(source_template.pk)],
            "products": [],
            "employees": [],
        },
    )

    assert response.status_code == 302
    assert OdooSaleOrderTemplate.objects.count() == 1


@pytest.mark.django_db
def test_setup_templates_step_two_rejects_products_without_odoo_payload(admin_client):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    product = OdooProduct.objects.create(
        name="Local Only",
        renewal_period=30,
        odoo_product=None,
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

    assert response.status_code == 200
    assert "Select only products imported from Odoo" in response.content.decode()
    assert OdooSaleOrderTemplate.objects.filter(name="Setup: Base").count() == 0
    assert OdooSaleFactor.objects.count() == 0
    assert OdooSaleFactorProductRule.objects.count() == 0


@pytest.mark.django_db
def test_setup_templates_step_two_rechecks_product_payload_before_rule_creation(
    admin_client, monkeypatch
):
    source_template = OdooSaleOrderTemplate.objects.create(
        name="Base",
        odoo_template={"id": 90, "name": "Base"},
    )
    product = OdooProduct.objects.create(
        name="Addon",
        renewal_period=30,
        odoo_product={"id": 501, "name": "Addon"},
    )
    checks = iter([True, True, False])

    monkeypatch.setattr(
        odoo_admin,
        "_get_valid_odoo_product_payload",
        lambda product: {"id": 501, "name": "Addon"} if next(checks) else None,
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
    assert response.url == reverse("admin:odoo_odoosaleordertemplate_setup_templates_create")
    assert OdooSaleOrderTemplate.objects.filter(name="Setup: Base").count() == 0
    assert OdooSaleFactor.objects.count() == 0
    assert OdooSaleFactorProductRule.objects.count() == 0
