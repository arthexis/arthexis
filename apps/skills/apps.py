from django.apps import AppConfig


class SkillsConfig(AppConfig):
    """Operator framework app for Skills, Agents, and Hooks."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.skills"
    label = "skills"
    verbose_name = "Operator Framework"
