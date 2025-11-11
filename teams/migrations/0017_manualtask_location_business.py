import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0097_location_business_model"),
        ("teams", "0016_move_email_profiles"),
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
                to="core.location",
                verbose_name="Location",
            ),
        ),
    ]
