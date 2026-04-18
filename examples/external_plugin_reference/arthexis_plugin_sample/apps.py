"""Plugin app configuration."""

from django.apps import AppConfig


class ArthexisPluginSampleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "arthexis_plugin_sample"
    verbose_name = "Arthexis Plugin Sample"
    arthexis_compatibility = ">=0.2,<0.3"
