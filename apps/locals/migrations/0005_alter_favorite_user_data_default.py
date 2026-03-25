from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("locals", "0004_mark_favorites_as_user_data"),
    ]

    operations = [
        migrations.AlterField(
            model_name="favorite",
            name="user_data",
            field=models.BooleanField(default=True),
        ),
    ]
