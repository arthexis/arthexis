from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TeamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teams"
    order = 6
    verbose_name = _("Workgroup")
