from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AwgConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "awg"
    order = 1
    verbose_name = _("Power")
