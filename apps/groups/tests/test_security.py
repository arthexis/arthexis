import json

from django.contrib.auth.models import Group
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


def test_security_group_fixture_loads_on_fresh_database(db):
    """Canonical security group fixture should create linked parent and child rows."""

    SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).delete()

    call_command("loaddata", "apps/groups/fixtures/security_groups__site_operator.json", verbosity=0)

    assert SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).count() == 1


def test_security_group_fixture_respects_explicit_pk(tmp_path, db):
    """Explicit fixture identities should remain stable for related fixture objects."""

    group_name = "Explicit Fixture Group"
    fixture_pk = 4242
    SecurityGroup.objects.filter(name=group_name).delete()
    Group.objects.filter(name=group_name).delete()
    fixture_path = tmp_path / "security_group.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "model": "groups.securitygroup",
                    "pk": fixture_pk,
                    "fields": {
                        "name": group_name,
                        "app": "",
                        "parent": None,
                        "site_template": None,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    call_command("loaddata", str(fixture_path), verbosity=0)

    assert SecurityGroup.objects.filter(pk=fixture_pk, name=group_name).exists()


def test_security_group_fixture_loads_when_group_name_already_exists(db):
    """Loading canonical security group fixture should be idempotent by group name."""

    SecurityGroup.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)

    call_command("loaddata", "apps/groups/fixtures/security_groups__site_operator.json", verbosity=0)

    assert SecurityGroup.objects.filter(name=SITE_OPERATOR_GROUP_NAME).count() == 1
