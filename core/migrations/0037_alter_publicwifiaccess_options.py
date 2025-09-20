from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_user_last_visit_ip_address"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="publicwifiaccess",
            options={
                "unique_together": {("user", "mac_address")},
                "verbose_name": "Wi-Fi Lease",
                "verbose_name_plural": "Wi-Fi Leases",
            },
        ),
    ]
