from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_user_address_user_has_charger"),
    ]

    operations = [
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("street", models.CharField(max_length=255)),
                ("number", models.CharField(max_length=20)),
                ("municipality", models.CharField(max_length=100)),
                ("state", models.CharField(max_length=2)),
                ("postal_code", models.CharField(max_length=10)),
            ],
        ),
        migrations.RemoveField(
            model_name="user",
            name="address",
        ),
        migrations.AddField(
            model_name="user",
            name="address",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.address"),
        ),
    ]
