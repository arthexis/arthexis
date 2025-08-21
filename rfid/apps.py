from django.apps import AppConfig


class RfidConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rfid"

    def ready(self):  # pragma: no cover - startup side effects
        from .background_reader import start

        start()
