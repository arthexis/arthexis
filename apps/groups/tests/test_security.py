from django.contrib.auth import get_user_model

from apps.groups.constants import (
    EXTERNAL_AGENT_GROUP_NAME,
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
