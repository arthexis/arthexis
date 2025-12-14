from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("odoo", "0004_odoodeployment"),
    ]

    operations = [
        migrations.AddField(
            model_name="odoodeployment",
            name="base_path",
            field=models.CharField(
                blank=True,
                help_text="Directory that contains the odoo configuration file.",
                max_length=500,
                default="",
            ),
        ),
    ]
