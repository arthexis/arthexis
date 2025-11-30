from django.apps import AppConfig


class OcppSimulatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ocpp.simulator"
    label = "ocpp_simulator"
    verbose_name = "OCPP Simulator"
