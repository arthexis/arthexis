from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0004_alter_odoochatbridge_profile"),
        ("counters", "0002_copy_dashboard_data"),
    ]

    operations = [
        migrations.DeleteModel(name="DashboardRule"),
    ]
