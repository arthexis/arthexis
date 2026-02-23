"""Application configuration for the GraphQL integration."""

from django.apps import AppConfig


class GraphqlConfig(AppConfig):
    """Register the GraphQL integration app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.graphql"
    verbose_name = "GraphQL"
