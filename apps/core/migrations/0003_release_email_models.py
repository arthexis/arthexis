from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_initial"),
        ("emails", "0004_adopt_core_email_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EmailTransactionAttachment"),
                migrations.DeleteModel(name="EmailTransaction"),
                migrations.DeleteModel(name="EmailArtifact"),
            ],
        )
    ]
