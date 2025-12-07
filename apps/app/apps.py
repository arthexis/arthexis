from django.apps import AppConfig as BaseAppConfig
from django.db import models


class AppConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.app"

    def ready(self):
        from apps.app import signals  # noqa: F401
