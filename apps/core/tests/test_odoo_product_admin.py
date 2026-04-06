from __future__ import annotations

from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

import pytest

from apps.odoo.models import OdooEmployee
from apps.users.models import User


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
