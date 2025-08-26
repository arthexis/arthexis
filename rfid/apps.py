from django.apps import AppConfig


class RfidConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rfid"

    def ready(self):  # pragma: no cover - startup side effects
        from .background_reader import start
        from .signals import tag_scanned
        from msg import notify

        def _notify(_sender, rfid=None, **_kwargs):
            if rfid:
                notify("RFID", str(rfid))

        tag_scanned.connect(_notify, weak=False)

        start()
