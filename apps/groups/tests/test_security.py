from django.contrib.auth import get_user_model

from apps.groups.constants import (
    EXTERNAL_AGENT_GROUP_NAME,
    NETWORK_OPERATOR_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
    SITE_OPERATOR_GROUP_NAME,
)
from apps.groups.models import SecurityGroup
from apps.groups.security import ensure_default_staff_groups, ensure_security_groups_exist


def test_security_group_labels_distinguish_basic_and_user_facing(db):
    """Canonical staff groups should be labeled differently from other SGs."""

    basic_group = SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
    user_facing_group = SecurityGroup.objects.create(name="Customer Portal")

    assert basic_group.security_model_label == "Basic staff security group"
    assert user_facing_group.security_model_label == "User-facing security group"


def test_ensure_security_groups_exist_repairs_missing_child_rows(db):
    """Canonical groups should always exist as concrete ``SecurityGroup`` rows."""

    SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
    SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).delete()

    group = ensure_security_groups_exist([SITE_OPERATOR_GROUP_NAME])[SITE_OPERATOR_GROUP_NAME]

    assert isinstance(group, SecurityGroup)
    assert SecurityGroup.objects.filter(pk=group.pk, name=SITE_OPERATOR_GROUP_NAME).exists()


def test_admin_user_gets_site_operator_default_group(db):
    """The built-in admin account should always receive Site Operator."""

    user = get_user_model().objects.create_superuser(username="admin", password="admin")

    assert user.groups.filter(name=SITE_OPERATOR_GROUP_NAME).exists()


def test_arthexis_user_gets_operator_developer_release_defaults(db):
    """The built-in system account should receive its canonical staff groups."""

    user = get_user_model().objects.create_superuser(username="arthexis", password="admin")

    assert user.groups.filter(name=NETWORK_OPERATOR_GROUP_NAME).exists()
    assert user.groups.filter(name=PRODUCT_DEVELOPER_GROUP_NAME).exists()
    assert user.groups.filter(name=RELEASE_MANAGER_GROUP_NAME).exists()


def test_generic_staff_user_defaults_to_external_agent_without_explicit_groups(db):
    """Ordinary staff accounts should fall back to External Agent."""

    user = get_user_model().objects.create_user(username="staff-default", is_staff=True)
    user.save(update_fields=["is_staff"])

    added = ensure_default_staff_groups(user)

    assert added == (EXTERNAL_AGENT_GROUP_NAME,)
    assert user.groups.filter(name=EXTERNAL_AGENT_GROUP_NAME).exists()


def test_explicit_groups_skip_external_agent_default(db):
    """Explicit group assignment should suppress the generic External Agent default."""

    user = get_user_model().objects.create_user(username="staff-explicit", is_staff=True)
    user.save(update_fields=["is_staff"])

    added = ensure_default_staff_groups(user, explicit_group_names=["Customer Portal"])

    assert added == ()
    assert not user.groups.filter(name=EXTERNAL_AGENT_GROUP_NAME).exists()
