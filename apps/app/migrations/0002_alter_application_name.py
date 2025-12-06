from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="name",
            field=models.CharField(blank=True, max_length=100, unique=True),
        ),
    ]
