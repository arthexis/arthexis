from django.apps import AppConfig


class SimulatorsConfig(AppConfig):
    """Configuration for simulator utilities and schedules."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.simulators"
    label = "simulators"
    verbose_name = "Simulators"
