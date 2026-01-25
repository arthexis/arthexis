from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nginx", "0005_siteconfiguration_managed_subdomains"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="siteconfiguration",
            name="secondary_instance",
        ),
    ]
