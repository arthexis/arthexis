from django.apps import AppConfig


class OcppForwarderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ocpp.forwarder"
    label = "ocpp_forwarder"
    verbose_name = "OCPP Forwarder"
