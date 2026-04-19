from django.apps import AppConfig


class SurveyConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.survey"
    label = "survey"
    verbose_name = "Survey"
