"""Django app configuration for liboqs integration."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LiboqsConfig(AppConfig):
    """Register the liboqs integration app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.liboqs"
    verbose_name = _("liboqs")
