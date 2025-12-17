from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CertsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.certs"
    verbose_name = _("Certificates")
