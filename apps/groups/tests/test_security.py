from django.contrib.auth import get_user_model

from apps.groups.constants import (
    EXTERNAL_AGENT_GROUP_NAME,
    SITE_OPERATOR_GROUP_NAME,
)
from apps.groups.models import SecurityGroup
from apps.groups.security import ensure_default_staff_groups, ensure_security_groups_exist

def test_ensure_security_groups_exist_repairs_missing_child_rows(db):
    """Canonical groups should always exist as concrete ``SecurityGroup`` rows."""

    SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
    SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).delete()

    group = ensure_security_groups_exist([SITE_OPERATOR_GROUP_NAME])[SITE_OPERATOR_GROUP_NAME]

    assert isinstance(group, SecurityGroup)
    assert SecurityGroup.objects.filter(pk=group.pk, name=SITE_OPERATOR_GROUP_NAME).exists()
