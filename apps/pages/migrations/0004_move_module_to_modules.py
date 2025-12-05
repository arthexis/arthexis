import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0001_initial"),
        ("pages", "0003_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="landing",
                    name="module",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="landings",
                        to="modules.module",
                    ),
                ),
                migrations.DeleteModel(name="Module"),
            ],
            database_operations=[],
        ),
    ]
