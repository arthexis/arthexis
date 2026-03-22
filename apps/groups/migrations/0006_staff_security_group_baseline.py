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

REVERSE_LEGACY_GROUP_NAME_MAP = {
    canonical_name: legacy_name for legacy_name, canonical_name in LEGACY_GROUP_NAME_MAP.items()
}

PRESERVED_CANONICAL_GROUP_NAMES = {"Site Operator"}


def _move_members_and_permissions(*, alias, source_group, target_group) -> None:
    """Move memberships and permissions from one group to another."""

    permissions = list(source_group.permissions.using(alias).all())
    if permissions:
        target_group.permissions.add(*permissions)
        source_group.permissions.remove(*permissions)

    users = list(source_group.user_set.using(alias).all())
    for user in users:
        user.groups.add(target_group)
        user.groups.remove(source_group)


def _repoint_related_group_references(*, alias, source_group, target_group, apps) -> None:
    """Move all model fields that still point at ``source_group`` onto ``target_group``."""

    security_group_model = source_group.__class__

    for model in apps.get_models():
        if model is security_group_model:
            continue
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue

        for field in model._meta.get_fields():
            if not getattr(field, "is_relation", False):
                continue
            related_model = getattr(field, "related_model", None)
            if related_model is None or not issubclass(security_group_model, related_model):
                continue
            if field.auto_created and not field.concrete:
                continue

            if field.many_to_one or field.one_to_one:
                manager.using(alias).filter(**{field.name: source_group}).update(**{field.name: target_group})
                continue

            if not field.many_to_many:
                continue

            for related_object in manager.using(alias).filter(**{field.name: source_group}).distinct():
                related_manager = getattr(related_object, field.name)
                related_manager.add(target_group)
                related_manager.remove(source_group)


def _group_has_related_references(*, alias, group, apps) -> bool:
    """Return whether any concrete model field still references ``group``."""

    security_group_model = group.__class__

    for model in apps.get_models():
        if model is security_group_model:
            continue
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue

        for field in model._meta.get_fields():
            if not getattr(field, "is_relation", False):
                continue
            related_model = getattr(field, "related_model", None)
            if related_model is None or not issubclass(security_group_model, related_model):
                continue
            if field.auto_created and not field.concrete:
                continue
            if manager.using(alias).filter(**{field.name: group}).exists():
                return True

    return False


def _move_group_data(*, alias, source_group, target_group, apps) -> None:
    """Move memberships, permissions, and related-object references to another group."""

    _move_members_and_permissions(
        alias=alias,
        source_group=source_group,
        target_group=target_group,
    )
    _repoint_related_group_references(
        alias=alias,
        source_group=source_group,
        target_group=target_group,
        apps=apps,
    )


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
        _move_group_data(
            alias=alias,
            source_group=legacy_group,
            target_group=canonical_group,
            apps=apps,
        )
        legacy_group.delete()


def unseed_staff_security_groups(apps, schema_editor):
    """Restore legacy groups and remove canonical groups that only exist for this migration."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    alias = schema_editor.connection.alias

    canonical_groups = {
        name: SecurityGroup.objects.using(alias).filter(name=name).first()
        for name in STAFF_GROUP_NAMES
    }

    for canonical_name, legacy_name in REVERSE_LEGACY_GROUP_NAME_MAP.items():
        canonical_group = canonical_groups.get(canonical_name)
        if canonical_group is None:
            continue
        legacy_group, _ = SecurityGroup.objects.using(alias).get_or_create(name=legacy_name)
        _move_group_data(
            alias=alias,
            source_group=canonical_group,
            target_group=legacy_group,
            apps=apps,
        )
        canonical_group.refresh_from_db()
        if (
            not canonical_group.user_set.using(alias).exists()
            and not canonical_group.permissions.using(alias).exists()
            and not _group_has_related_references(alias=alias, group=canonical_group, apps=apps)
        ):
            canonical_group.delete()

    for name in reversed(STAFF_GROUP_NAMES):
        if name in PRESERVED_CANONICAL_GROUP_NAMES or name in REVERSE_LEGACY_GROUP_NAME_MAP:
            continue
        group = SecurityGroup.objects.using(alias).filter(name=name).first()
        if group is None:
            continue
        if group.user_set.using(alias).exists() or group.permissions.using(alias).exists():
            continue
        if _group_has_related_references(alias=alias, group=group, apps=apps):
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
