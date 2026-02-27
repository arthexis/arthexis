"""Add secure dashboard token for public Evergo profile view links."""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("evergo", "0007_evergoartifact"),
    ]

    operations = [
        migrations.AddField(
            model_name="evergouser",
            name="dashboard_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
