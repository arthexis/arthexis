from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0002_alter_module_application_delete_application"),
        ("sigils", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="CustomSigil")],
            database_operations=[],
        )
    ]
