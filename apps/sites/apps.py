import logging

from django.apps import AppConfig
from django.core.signals import request_started
from django.db import DatabaseError
from django.db.models.signals import post_migrate


logger = logging.getLogger(__name__)


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sites"
    label = "pages"
    _view_history_purged = False

    def ready(self):  # pragma: no cover - import for side effects
        from . import checks  # noqa: F401
        from . import site_config
        from . import widgets  # noqa: F401
        from .loader import ensure_site_badges_exist, load_admin_badge_seed_data

        site_config.ready()
        post_migrate.connect(
            ensure_site_badges_exist,
            sender=self,
            dispatch_uid="pages_ensure_site_badges_exist",
            weak=False,
        )
        post_migrate.connect(
            load_admin_badge_seed_data,
            sender=self,
            dispatch_uid="pages_load_admin_badge_seed_data",
            weak=False,
        )
        request_started.connect(
            self._handle_request_started,
            dispatch_uid="pages_view_history_request_started",
            weak=False,
        )

    def _handle_request_started(self, sender, **kwargs):
        if self._view_history_purged:
            return
        self._view_history_purged = True
        self._purge_view_history()

    def _purge_view_history(self, days: int = 15) -> None:
        """Remove stale :class:`apps.sites.models.ViewHistory` entries."""

        from .models import ViewHistory

        try:
            deleted = ViewHistory.purge_older_than(days=days)
        except DatabaseError:
            logger.debug("Skipping view history purge; database unavailable", exc_info=True)
        else:
            if deleted:
                logger.info("Purged %s view history entries older than %s days", deleted, days)
