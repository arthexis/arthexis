from django.core.management import call_command

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup
from apps.groups.security import ensure_security_groups_exist


def test_ensure_security_groups_exist_repairs_missing_child_rows(db):
    """Canonical groups should always exist as concrete ``SecurityGroup`` rows."""

    SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
    SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).delete()

    group = ensure_security_groups_exist([SITE_OPERATOR_GROUP_NAME])[SITE_OPERATOR_GROUP_NAME]

    assert isinstance(group, SecurityGroup)
    assert SecurityGroup.objects.filter(pk=group.pk, name=SITE_OPERATOR_GROUP_NAME).exists()



def test_security_group_fixture_loads_when_group_name_already_exists(db):
    """Loading canonical security group fixture should be idempotent by group name."""

    SecurityGroup.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)

    call_command("loaddata", "apps/groups/fixtures/security_groups__site_operator.json", verbosity=0)

    assert SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).count() == 1
