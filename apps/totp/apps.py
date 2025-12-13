from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TotpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.totp"
    verbose_name = _("TOTP")
