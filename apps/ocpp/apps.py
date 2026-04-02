from django.apps import AppConfig


class OcppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ocpp"
    label = "ocpp"
    verbose_name = "OCPP"

    def ready(self):  # pragma: no cover - startup import side effects
        from . import signals  # noqa: F401
