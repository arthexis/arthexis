from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    label = "reports"

    def ready(self):
        try:
            from .system import patch_admin_system_view
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unable to patch admin system views: %s", exc)
        else:
            patch_admin_system_view()
