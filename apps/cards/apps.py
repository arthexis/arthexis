from pathlib import Path

from django.apps import AppConfig
from django.conf import settings


class CardsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cards"
    label = "cards"

    def ready(self):  # pragma: no cover - startup side effects
        control_lock = Path(settings.BASE_DIR) / ".locks" / "control.lck"
        rfid_lock = Path(settings.BASE_DIR) / ".locks" / "rfid.lck"
        if not (control_lock.exists() and rfid_lock.exists()):
            return

        from apps.core.notifications import notify
        from .signals import tag_scanned

        def _notify(_sender, rfid=None, **_kwargs):
            if rfid:
                notify("RFID", str(rfid))

        tag_scanned.connect(_notify, weak=False)
