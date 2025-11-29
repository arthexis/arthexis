from django.apps import AppConfig


class ProtocolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "protocols"
    order = 3
    verbose_name = "Protocol"
