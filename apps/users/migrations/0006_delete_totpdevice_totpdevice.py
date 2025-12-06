from django.conf import settings
from django.db import migrations


def _otp_totp_installed():
    installed_apps = set(settings.INSTALLED_APPS)
    return "otp_totp" in installed_apps or "django_otp.plugins.otp_totp" in installed_apps


class Migration(migrations.Migration):

    if _otp_totp_installed():
        dependencies = [
            ("otp_totp", "0003_add_timestamps"),
            ("users", "0005_delete_totpdevice_totpdevice"),
        ]

        operations = [
            migrations.DeleteModel(
                name="TOTPDevice",
            ),
            migrations.CreateModel(
                name="TOTPDevice",
                fields=[],
                options={
                    "proxy": True,
                    "indexes": [],
                    "constraints": [],
                },
                bases=("otp_totp.totpdevice",),
            ),
        ]
    else:
        dependencies = [("users", "0005_delete_totpdevice_totpdevice")]
        operations = []
