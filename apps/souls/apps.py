from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SoulsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.souls"
    verbose_name = _("Souls")
