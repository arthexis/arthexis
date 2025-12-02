from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0002_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="DeveloperArticle",
        ),
    ]
