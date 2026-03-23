"""Legacy Mermaid app retained only for migration compatibility."""

from django.apps import AppConfig


class MermaidConfig(AppConfig):
    """Migration-only Mermaid app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "config.legacy_mermaid"
    label = "mermaid"
    verbose_name = "Mermaid"
