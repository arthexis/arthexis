from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup


def test_site_operator_staff_cannot_create_security_groups(db):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="local-admin",
        password="test-pass",
        is_staff=True,
    )
    security_group_permissions = Permission.objects.filter(
        content_type__app_label="groups",
        content_type__model="securitygroup",
    )
    user.user_permissions.add(*security_group_permissions)
    site_operator_group, _ = SecurityGroup.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)
    user.groups.add(site_operator_group)

    client = Client()
    client.force_login(user)

    response = client.get(reverse("admin:groups_securitygroup_add"))

    assert response.status_code == 403


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
