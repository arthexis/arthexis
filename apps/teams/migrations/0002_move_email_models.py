from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0001_initial"),
        ("teams", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="EmailInbox"),
                migrations.DeleteModel(name="EmailCollector"),
                migrations.DeleteModel(name="EmailOutbox"),
            ],
            database_operations=[],
        )
    ]
