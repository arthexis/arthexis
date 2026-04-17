from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jobs"
    label = "jobs"
    verbose_name = "Jobs"
