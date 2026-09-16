from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nginx", "0002_remove_siteconfiguration_certificate"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SiteConfiguration",
        ),
    ]
