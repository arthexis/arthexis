from django.apps import AppConfig
from pathlib import Path
from django.conf import settings


class OcppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ocpp"
    verbose_name = "OCPP"

    def ready(self):  # pragma: no cover - startup side effects
        lock = Path(settings.BASE_DIR) / "locks" / "control.lck"
        if not lock.exists():
            return
        from .rfid.background_reader import start
        from .rfid.signals import tag_scanned
        from core.notifications import notify

        def _notify(_sender, rfid=None, **_kwargs):
            if rfid:
                notify("RFID", str(rfid))

        tag_scanned.connect(_notify, weak=False)
        start()
