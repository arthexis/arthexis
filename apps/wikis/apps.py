from django.apps import AppConfig


class WikisConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wikis"
    label = "wikis"
    verbose_name = "Wikis"

    def ready(self):  # pragma: no cover - import for side effects
        from . import widgets  # noqa: F401
