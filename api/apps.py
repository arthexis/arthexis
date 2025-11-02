from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Configuration for the GraphQL API app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "GraphQL API"
