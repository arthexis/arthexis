from django.db import migrations


CHARGE_STATION_MANAGER_GROUP_NAME = "Charge Station Manager"
OCPP_APP_LABEL = "ocpp"


def add_charge_station_manager_group(apps, schema_editor):
    """Create the Charge Station Manager security group when absent."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    alias = schema_editor.connection.alias
    SecurityGroup.objects.using(alias).get_or_create(
        name=CHARGE_STATION_MANAGER_GROUP_NAME,
        defaults={"app": OCPP_APP_LABEL},
    )


def remove_charge_station_manager_group(apps, schema_editor):
    """Delete the Charge Station Manager security group created by this migration."""

    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    alias = schema_editor.connection.alias
    SecurityGroup.objects.using(alias).filter(
        name=CHARGE_STATION_MANAGER_GROUP_NAME,
        app=OCPP_APP_LABEL,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0004_securitygroup_app"),
    ]

    operations = [
        migrations.RunPython(
            add_charge_station_manager_group,
            remove_charge_station_manager_group,
        ),
    ]
