"""Remove the retired Raspberry Pi image-management schema."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("imager", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="RaspberryPiImageBurnJob"),
        migrations.DeleteModel(name="RaspberryPiImageArtifact"),
    ]
