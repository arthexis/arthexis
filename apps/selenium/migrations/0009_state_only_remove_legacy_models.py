from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("selenium", "0008_remove_sessioncookie_selenium_sessioncookie_owner_exclusive_and_more"),
        ("playwright", "0002_migrate_from_selenium"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="SeleniumBrowser"),
                migrations.DeleteModel(name="SeleniumScript"),
                migrations.DeleteModel(name="SessionCookie"),
            ],
        ),
    ]
