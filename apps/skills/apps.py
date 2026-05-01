from django.apps import AppConfig


class SkillsConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.skills"
    label = "skills"
    verbose_name = "Skills"
