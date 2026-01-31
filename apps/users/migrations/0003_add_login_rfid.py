from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0004_cardface_media_background"),
        ("users", "0002_totp"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="login_rfid",
            field=models.ForeignKey(
                blank=True,
                db_column="login_rfid_key",
                help_text="RFID card assigned for RFID logins.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="login_users",
                to="cards.rfid",
            ),
        ),
    ]
