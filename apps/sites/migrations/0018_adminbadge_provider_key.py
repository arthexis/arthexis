from django.db import migrations, models


PATH_TO_PROVIDER_KEY = {
    "apps.sites.admin_badges.node_badge_data": "node",
    "apps.sites.admin_badges.role_badge_data": "role",
    "apps.sites.admin_badges.site_badge_data": "site",
}


def migrate_provider_keys(apps, schema_editor):
    """Backfill provider keys from legacy dotted callable paths."""

    AdminBadge = apps.get_model("pages", "AdminBadge")
    for path, provider_key in PATH_TO_PROVIDER_KEY.items():
        AdminBadge.objects.filter(value_query_path=path).update(provider_key=provider_key)


def reverse_migrate_provider_keys(apps, schema_editor):
    """Restore legacy dotted paths from provider keys."""

    AdminBadge = apps.get_model("pages", "AdminBadge")
    for path, provider_key in PATH_TO_PROVIDER_KEY.items():
        AdminBadge.objects.filter(provider_key=provider_key).update(value_query_path=path)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0017_alter_userstory_screenshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminbadge",
            name="provider_key",
            field=models.CharField(
                choices=[("site", "Site"), ("node", "Node"), ("role", "Role")],
                default="site",
                max_length=20,
            ),
        ),
        migrations.RunPython(migrate_provider_keys, reverse_migrate_provider_keys),
        migrations.AlterField(
            model_name="adminbadge",
            name="value_query_path",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Deprecated: provider source is configured via Provider key.",
                max_length=255,
            ),
        ),
    ]
