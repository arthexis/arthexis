from __future__ import annotations

from types import SimpleNamespace

from django import forms

import pytest

from apps.core.admin.forms import (
    KeepExistingValue,
    MaskedPasswordFormMixin,
    OdooEmployeeAdminForm,
)
from apps.odoo.models import OdooEmployee


@pytest.mark.django_db
def test_odoo_employee_admin_form_requires_password_on_create(admin_user):
    """Creating an Odoo employee requires a password."""

    form = OdooEmployeeAdminForm(
        data={
            "user": str(admin_user.pk),
            "host": "https://odoo.example.com",
            "database": "odoodb",
            "username": "admin",
            "password": "",
        }
    )

    assert not form.is_valid()
    assert "password" in form.errors


@pytest.mark.django_db
def test_odoo_employee_admin_form_keeps_existing_password_for_updates(admin_user):
    """Blank password edits keep the stored credential value."""

    employee = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="stored-secret",
    )
    form = OdooEmployeeAdminForm(
        instance=employee,
        data={
            "user": str(admin_user.pk),
            "host": "https://odoo.example.com",
            "database": "odoodb",
            "username": "admin",
            "password": "",
        },
    )

    assert form.is_valid(), form.errors
    assert isinstance(form.cleaned_data["password"], KeepExistingValue)
    assert form.instance.password == "stored-secret"


@pytest.mark.django_db
class TestMaskedPasswordFormMixinSupport:
    def test_odoo_employee_password_widget_keeps_render_value_true(self):
        form = OdooEmployeeAdminForm()

        assert isinstance(form.fields["password"].widget, forms.PasswordInput)
        assert form.fields["password"].widget.render_value is True

    def test_configurable_password_field_name_uses_keep_existing_on_update(self):
        class DummyForm(MaskedPasswordFormMixin, forms.Form):
            password_field_name = "secret"
            secret = forms.CharField(required=False)

            def __init__(self, *args, instance=None, **kwargs):
                self.instance = instance or SimpleNamespace(pk=None)
                super().__init__(*args, **kwargs)

        form = DummyForm(data={"secret": ""}, instance=SimpleNamespace(pk=1))

        assert form.is_valid(), form.errors
        assert isinstance(form.cleaned_data["secret"], KeepExistingValue)
