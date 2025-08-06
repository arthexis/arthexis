from django.apps import AppConfig


class RFIDConfig(AppConfig):
    """Configuration for the RFID app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "rfid"
    verbose_name = "RFID"
