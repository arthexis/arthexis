import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
        ("otp_totp", "0003_add_timestamps"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="TOTPDeviceSettings",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        (
                            "issuer",
                            models.CharField(
                                default="",
                                help_text="Label shown in authenticator apps. Leave blank to use Arthexis.",
                                max_length=64,
                                blank=True,
                            ),
                        ),
                        (
                            "allow_without_password",
                            models.BooleanField(
                                default=False,
                                help_text="Allow authenticator logins to skip the password step.",
                            ),
                        ),
                        (
                            "device",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="custom_settings",
                                to="otp_totp.totpdevice",
                            ),
                        ),
                        (
                            "security_group",
                            models.ForeignKey(
                                blank=True,
                                help_text="Share this authenticator with every user in the selected security group.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="totp_devices",
                                to="core.securitygroup",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Authenticator Device Setting",
                        "verbose_name_plural": "Authenticator Device Settings",
                        "db_table": "core_totpdevicesettings",
                    },
                ),
            ],
            database_operations=[],
        )
    ]
