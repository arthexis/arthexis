from django.apps import AppConfig


class FeaturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.features"
    label = "features"
    verbose_name = "Suite Features"

    def ready(self):  # pragma: no cover - import for side effects
        from . import widgets  # noqa: F401
