from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_merge_20251202_2107"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="GoogleCalendarProfile"),
                migrations.DeleteModel(name="PasskeyCredential"),
                migrations.DeleteModel(name="TOTPDeviceSettings"),
                migrations.DeleteModel(name="User"),
                migrations.DeleteModel(name="UserPhoneNumber"),
            ],
            database_operations=[],
        )
    ]
