import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0002_move_email_models"),
        ("maps", "0001_initial"),
        ("energy", "0003_move_location_to_maps"),
    ]

    operations = [
        migrations.AlterField(
            model_name="manualtask",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text="Location associated with this manual task.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="manual_tasks",
                to="maps.location",
                verbose_name="Location",
            ),
        ),
    ]
