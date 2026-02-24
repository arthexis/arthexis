from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("locals", "0003_hiddenadminapp"),
    ]

    operations = [
        migrations.AddField(
            model_name="hiddenadminapp",
            name="is_seed_data",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="hiddenadminapp",
            name="is_user_data",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AlterField(
            model_name="hiddenadminapp",
            name="is_deleted",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
