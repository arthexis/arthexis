"""Application configuration for prompt persistence."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PromptsConfig(AppConfig):
    """Register the prompts app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.prompts"
    verbose_name = _("Prompts")
