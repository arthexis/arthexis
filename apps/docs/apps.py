from django.apps import AppConfig as BaseAppConfig


class DocsConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.docs"
