from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("meta", "0004_alter_attention_options"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppSecretaryAuthorizedPhone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("phone", models.CharField(max_length=32, unique=True)),
                ("label", models.CharField(blank=True, max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whatsapp_secretary_phones",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "WhatsApp Secretary Authorized Phone",
                "verbose_name_plural": "WhatsApp Secretary Authorized Phones",
                "ordering": ["phone", "pk"],
            },
        ),
    ]
