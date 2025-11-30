from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ContentClassification"),
                migrations.DeleteModel(name="ContentClassifier"),
                migrations.DeleteModel(name="ContentSample"),
                migrations.DeleteModel(name="ContentTag"),
            ],
            database_operations=[],
        )
    ]
