from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("apis", "0003_generalservicetoken_generalservicetokenevent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generalservicetoken",
            name="expires_at",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name="generalservicetoken",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("retired", "Retired"),
                    ("revoked", "Revoked"),
                ],
                db_index=True,
                default="active",
                max_length=16,
            ),
        ),
    ]
