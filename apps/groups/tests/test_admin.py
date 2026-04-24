from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
import pytest

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup


def create_staff_user_with_security_group_permissions(username, *codenames):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username,
        password="test-pass",
        is_staff=True,
    )
    security_group_permissions = Permission.objects.filter(
        content_type__app_label="groups",
        content_type__model="securitygroup",
        codename__in=codenames,
    )
    user.user_permissions.add(*security_group_permissions)
    return user


def add_site_operator_membership(user):
    site_operator_group, _ = SecurityGroup.objects.get_or_create(
        name=SITE_OPERATOR_GROUP_NAME
    )
    user.groups.add(site_operator_group)


@pytest.mark.parametrize(
    ("is_site_operator", "expected_status"),
    (
        (True, 403),
        (False, 200),
    ),
)
def test_security_group_add_permission_for_staff_users(
    db, is_site_operator, expected_status
):
    user = create_staff_user_with_security_group_permissions(
        "local-admin" if is_site_operator else "regular-staff",
        "add_securitygroup",
    )
    if is_site_operator:
        add_site_operator_membership(user)

    client = Client()
    client.force_login(user)

    response = client.get(reverse("admin:groups_securitygroup_add"))

    assert response.status_code == expected_status


@pytest.mark.parametrize("is_site_operator", (True, False))
def test_staff_users_can_change_existing_security_groups(db, is_site_operator):
    user = create_staff_user_with_security_group_permissions(
        "local-admin" if is_site_operator else "regular-staff",
        "change_securitygroup",
    )
    if is_site_operator:
        add_site_operator_membership(user)
    managed_group = SecurityGroup.objects.create(name="managed-group")

    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("admin:groups_securitygroup_change", args=[managed_group.pk]),
        data={
            "name": "managed-group-updated",
            "permissions": [],
            "users": [],
        },
    )

    assert response.status_code == 302
    managed_group.refresh_from_db()
    assert managed_group.name == "managed-group-updated"


def test_superuser_can_still_create_security_groups(db):
    user_model = get_user_model()
    superuser = user_model.objects.create_superuser(
        username="global-admin",
        email="global-admin@example.com",
        password="test-pass",
    )

    client = Client()
    client.force_login(superuser)

    response = client.get(reverse("admin:groups_securitygroup_add"))

    assert response.status_code == 200
