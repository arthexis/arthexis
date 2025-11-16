from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.core.signals import request_started


class OcppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ocpp"
    verbose_name = "3. Protocol"

    def ready(self):  # pragma: no cover - startup side effects
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

        def _start_background_reader(**_kwargs):
            from .rfid.background_reader import start

            try:
                start()
            finally:
                request_started.disconnect(
                    receiver=_start_background_reader,
                    dispatch_uid="ocpp_rfid_start_on_request",
                )

        request_started.connect(
            _start_background_reader,
            dispatch_uid="ocpp_rfid_start_on_request",
            weak=False,
        )
