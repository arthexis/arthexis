from django.apps import AppConfig as BaseAppConfig
from django.db import models


class AppConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.app"
