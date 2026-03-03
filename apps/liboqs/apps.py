"""App configuration for liboqs integration."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LiboqsConfig(AppConfig):
    """Django configuration for liboqs capability tracking."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.liboqs"
    verbose_name = _("liboqs")
