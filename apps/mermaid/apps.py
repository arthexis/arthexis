from django.apps import AppConfig as BaseAppConfig


class MermaidConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mermaid"
