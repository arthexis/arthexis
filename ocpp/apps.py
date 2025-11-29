import logging
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.db import connections
from django.db.backends.signals import connection_created
from django.db.utils import OperationalError, ProgrammingError

from .status_resets import clear_cached_statuses

class OcppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ocpp"
    order = 3
    verbose_name = "Protocol"

    logger = logging.getLogger(__name__)
    _cleared_cached_statuses = False

    def ready(self):  # pragma: no cover - startup side effects
        connection_created.connect(
            self._clear_statuses_on_connection,
            dispatch_uid="ocpp.apps.clear_cached_statuses",
            weak=False,
        )

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

    def _clear_statuses_on_connection(self, sender, connection, **kwargs):
        if self._cleared_cached_statuses:
            return

        if connection.alias != "default":
            return

        self._cleared_cached_statuses = True
        try:
            self._clear_cached_statuses(connection)
        finally:
            connection_created.disconnect(
                receiver=self._clear_statuses_on_connection,
                dispatch_uid="ocpp.apps.clear_cached_statuses",
            )

    def _clear_cached_statuses(self, connection=None) -> None:
        """Reset persisted status fields on startup to avoid stale values."""

        connection = connection or connections["default"]
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

