from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_release_email_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="UsageEvent"),
                migrations.DeleteModel(name="InviteLead"),
                migrations.DeleteModel(name="AdminNotice"),
            ],
        ),
    ]
