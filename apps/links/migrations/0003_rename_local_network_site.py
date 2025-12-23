from django.db import migrations


LOCAL_NETWORK_DOMAIN = "192.168.129.10"
LOCAL_NETWORK_NAME = "Local Network"


def rename_local_network_site(apps, schema_editor) -> None:
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(domain=LOCAL_NETWORK_DOMAIN).exclude(
        name=LOCAL_NETWORK_NAME
    ).update(name=LOCAL_NETWORK_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("links", "0002_initial"),
        ("sites", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(rename_local_network_site, migrations.RunPython.noop),
    ]
