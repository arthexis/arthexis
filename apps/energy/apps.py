from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EnergyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.energy"
    label = "energy"
    order = 2
    verbose_name = _("Energy")
