from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("maps", "0002_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="googlemapslocation",
            name="coordinates",
        ),
        migrations.RemoveField(
            model_name="googlemapslocation",
            name="embed_url",
        ),
        migrations.RemoveField(
            model_name="googlemapslocation",
            name="formatted_address",
        ),
        migrations.RemoveField(
            model_name="googlemapslocation",
            name="map_url",
        ),
    ]
