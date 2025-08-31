from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BetaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "beta"
    verbose_name = _("Beta Channel")
