"""Add secure dashboard token for public Evergo profile view links."""

import uuid

from django.db import migrations, models


def backfill_dashboard_tokens(apps, schema_editor):
    """Populate a unique dashboard token for each pre-existing Evergo user."""
    del schema_editor
    EvergoUser = apps.get_model("evergo", "EvergoUser")
    for profile in EvergoUser.objects.filter(dashboard_token__isnull=True).iterator():
        profile.dashboard_token = uuid.uuid4()
        profile.save(update_fields=["dashboard_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("evergo", "0007_evergoartifact"),
    ]

    operations = [
        migrations.AddField(
            model_name="evergouser",
            name="dashboard_token",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(backfill_dashboard_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="evergouser",
            name="dashboard_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
