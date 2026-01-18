from __future__ import annotations

from django.db import migrations


def purge_release_managers(apps, schema_editor):
    ReleaseManager = apps.get_model("release", "ReleaseManager")
    ReleaseManager.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0006_package_oidc_publish_enabled"),
    ]

    operations = [
        migrations.RunPython(purge_release_managers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="package",
            name="release_manager",
        ),
        migrations.RemoveField(
            model_name="packagerelease",
            name="release_manager",
        ),
        migrations.DeleteModel(
            name="ReleaseManager",
        ),
    ]
