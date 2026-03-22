from django.db import migrations


STAFF_GROUP_NAMES = (
    "Site Operator",
    "Network Operator",
    "Product Developer",
    "Release Manager",
    "External Agent",
)

LEGACY_GROUP_NAME_MAP = {
    "Charge Station Manager": "Network Operator",
    "Odoo User": "External Agent",
}


def _move_members_and_permissions(*, alias, source_group, target_group) -> None:
    """Merge memberships and permissions from a legacy group into a canonical group."""

    for permission in source_group.permissions.using(alias).all():
        target_group.permissions.add(permission)

    for user in source_group.user_set.using(alias).all():
        user.groups.add(target_group)


def seed_staff_security_groups(apps, schema_editor):
    """Create the five canonical staff groups and merge legacy staff groups into them."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    alias = schema_editor.connection.alias

    canonical_groups = {}
    for name in STAFF_GROUP_NAMES:
        group, _ = SecurityGroup.objects.using(alias).get_or_create(name=name)
        canonical_groups[name] = group

    for legacy_name, canonical_name in LEGACY_GROUP_NAME_MAP.items():
        legacy_group = SecurityGroup.objects.using(alias).filter(name=legacy_name).first()
        if legacy_group is None:
            continue
        canonical_group = canonical_groups[canonical_name]
        _move_members_and_permissions(
            alias=alias,
            source_group=legacy_group,
            target_group=canonical_group,
        )
        legacy_group.delete()


def unseed_staff_security_groups(apps, schema_editor):
    """Remove only the canonical groups introduced by this migration when empty."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    alias = schema_editor.connection.alias

    for name in reversed(STAFF_GROUP_NAMES):
        group = SecurityGroup.objects.using(alias).filter(name=name).first()
        if group is None:
            continue
        if group.user_set.using(alias).exists() or group.permissions.using(alias).exists():
            continue
        group.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0005_charge_station_manager_group"),
    ]

    operations = [
        migrations.RunPython(
            seed_staff_security_groups,
            unseed_staff_security_groups,
        ),
    ]
