from django.apps import AppConfig as BaseAppConfig
from django.db import models


class AppConfig(BaseAppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app"

    def ready(self):  # pragma: no cover - runtime patch
        from django.contrib.sites.models import Site

        if not hasattr(Site, "is_seed_data"):
            Site.add_to_class("is_seed_data", models.BooleanField(default=False))

