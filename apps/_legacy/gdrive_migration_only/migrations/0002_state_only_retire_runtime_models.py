from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """Retire gdrive runtime models from migration state without touching legacy tables."""

    dependencies = [
        ("calendars", "0003_google_account_localization_and_gdrive_retirement"),
        ("gdrive", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="GoogleSheetColumn"),
                migrations.DeleteModel(name="GoogleSheet"),
                migrations.DeleteModel(name="GoogleAccount"),
            ],
        ),
    ]
