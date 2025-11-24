import logging
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError

from .status_resets import clear_cached_statuses


class OcppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ocpp"
    verbose_name = "3. Protocol"

    logger = logging.getLogger(__name__)

    def ready(self):  # pragma: no cover - startup side effects
        self._clear_cached_statuses()

        control_lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
        rfid_lock = Path(settings.BASE_DIR) / "locks" / "rfid.lck"
        if not (control_lock.exists() and rfid_lock.exists()):
            return
        from .rfid.signals import tag_scanned
        from core.notifications import notify

        def _notify(_sender, rfid=None, **_kwargs):
            if rfid:
                notify("RFID", str(rfid))

        tag_scanned.connect(_notify, weak=False)

    def _clear_cached_statuses(self) -> None:
        """Reset persisted status fields on startup to avoid stale values."""

        try:
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))
        except (OperationalError, ProgrammingError):
            return

        if "ocpp_charger" not in tables:
            return

        try:
            cleared = clear_cached_statuses()
        except Exception:  # pragma: no cover - defensive logging
            self.logger.exception("Failed to clear cached charger statuses on startup")
            return

        if cleared:
            self.logger.info("Cleared cached charger statuses for %s charge points", cleared)

