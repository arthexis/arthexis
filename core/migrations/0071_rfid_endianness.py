from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0070_alter_releasemanager_pypi_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfid",
            name="endianness",
            field=models.CharField(
                choices=[("BIG", "Big endian"), ("LITTLE", "Little endian")],
                default="BIG",
                max_length=6,
            ),
            preserve_default=False,
        ),
    ]
