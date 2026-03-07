"""Application configuration for classification orchestration."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ClassificationConfig(AppConfig):
    """Register classification models and signals."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.classification"
    verbose_name = _("Classification")

    def ready(self) -> None:
        """Import signals at startup."""

        from . import signals  # noqa: F401
