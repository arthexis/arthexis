from django.apps import AppConfig


class WebsiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "website"

    def ready(self):  # pragma: no cover - import for side effects
        from . import checks  # noqa: F401
