# Generated manually to extend django.contrib.sites.Site

from django.db import migrations, models


def add_site_fields(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    fields = [
        (
            "managed",
            models.BooleanField(
                default=False,
                db_default=False,
                verbose_name="Managed by local NGINX",
                help_text="Include this site when staging the local NGINX configuration.",
            ),
        ),
        (
            "require_https",
            models.BooleanField(
                default=False,
                db_default=False,
                verbose_name="Require HTTPS",
                help_text="Redirect HTTP traffic to HTTPS when the staged NGINX configuration is applied.",
            ),
        ),
    ]

    for name, field in fields:
        field.set_attributes_from_name(name)
        schema_editor.add_field(Site, field)


def remove_site_fields(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    for field_name in ["require_https", "managed"]:
        try:
            field = Site._meta.get_field(field_name)
        except Exception:  # pragma: no cover - defensive when downgrading
            continue
        schema_editor.remove_field(Site, field)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0020_userstory_assign_to_userstory_created_on_and_more"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunPython(add_site_fields, remove_site_fields),
    ]
