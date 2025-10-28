import logging

from django.apps import AppConfig
from django.db import DatabaseError
from django.db.backends.signals import connection_created


logger = logging.getLogger(__name__)


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pages"
    verbose_name = "7. Experience"
    _view_history_purged = False

    def ready(self):  # pragma: no cover - import for side effects
        from . import checks  # noqa: F401
        from . import site_config

        site_config.ready()
        connection_created.connect(
            self._handle_connection_created,
            dispatch_uid="pages_view_history_connection_created",
            weak=False,
        )

    def _handle_connection_created(self, sender, connection, **kwargs):
        if self._view_history_purged:
            return
        self._view_history_purged = True
        self._purge_view_history()

    def _purge_view_history(self, days: int = 15) -> None:
        """Remove stale :class:`pages.models.ViewHistory` entries."""

        from .models import ViewHistory

        try:
            deleted = ViewHistory.purge_older_than(days=days)
        except DatabaseError:
            logger.debug("Skipping view history purge; database unavailable", exc_info=True)
        else:
            if deleted:
                logger.info("Purged %s view history entries older than %s days", deleted, days)
