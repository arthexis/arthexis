from django.db import migrations


OLD_SLUG = "rfid-auth-audit-suite"
NEW_SLUG = "rfid-auth-audit"


def forward_rename_feature_slug(apps, schema_editor):
    """Rename legacy RFID auth audit suite feature slug to the canonical slug."""

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    legacy = Feature.objects.using(db_alias).filter(slug=OLD_SLUG).first()
    current = Feature.objects.using(db_alias).filter(slug=NEW_SLUG).first()

    if legacy and current:
        current.is_enabled = legacy.is_enabled
        current.summary = current.summary or legacy.summary
        current.admin_requirements = current.admin_requirements or legacy.admin_requirements
        current.public_requirements = current.public_requirements or legacy.public_requirements
        current.service_requirements = current.service_requirements or legacy.service_requirements
        current.save(
            update_fields=[
                "is_enabled",
                "summary",
                "admin_requirements",
                "public_requirements",
                "service_requirements",
            ]
        )
        legacy.delete()
        return

    if legacy and not current:
        legacy.slug = NEW_SLUG
        legacy.display = "RFID Auth Audit"
        legacy.save(update_fields=["slug", "display"])


def backward_rename_feature_slug(apps, schema_editor):
    """Restore the legacy RFID auth audit suite feature slug."""

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    current = Feature.objects.using(db_alias).filter(slug=NEW_SLUG).first()
    legacy = Feature.objects.using(db_alias).filter(slug=OLD_SLUG).first()

    if current and legacy:
        return

    if current and not legacy:
        current.slug = OLD_SLUG
        current.display = "RFID Auth Audit Suite"
        current.save(update_fields=["slug", "display"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0031_seed_rfid_auth_audit_suite_feature"),
    ]

    operations = [
        migrations.RunPython(forward_rename_feature_slug, backward_rename_feature_slug),
    ]
