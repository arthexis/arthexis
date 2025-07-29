from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_account"),
    ]

    operations = [
        migrations.CreateModel(
            name="RFID",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.CharField(max_length=64, unique=True)),
                ("blacklisted", models.BooleanField(default=False)),
                ("added_on", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rfids", to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
        migrations.RemoveField(
            model_name="user",
            name="rfid_uid",
        ),
        migrations.DeleteModel(
            name="BlacklistedRFID",
        ),
    ]

