"""Remove the retired Raspberry Pi Connect fleet-management schema."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("rpiconnect", "0002_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="ConnectCampaignEvent"),
        migrations.DeleteModel(name="ConnectIngestionEvent"),
        migrations.DeleteModel(name="ConnectUpdateDeployment"),
        migrations.DeleteModel(name="ConnectUpdateCampaign"),
        migrations.DeleteModel(name="ConnectDevice"),
        migrations.DeleteModel(name="ConnectImageRelease"),
        migrations.DeleteModel(name="ConnectAccount"),
    ]
