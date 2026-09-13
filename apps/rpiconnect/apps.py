from django.apps import AppConfig


class RpiconnectConfig(AppConfig):
    """Migration-only compatibility shell for the retired rpiconnect app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rpiconnect"
    label = "rpiconnect"
    verbose_name = "Rpiconnect (retired)"
