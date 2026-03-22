"""App configuration for project bundles."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Configure project bundle models and admin."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.projects"
    label = "projects"
    verbose_name = "Projects"
