from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sites"
    label = "pages"

    def ready(self):  # pragma: no cover - import for side effects
        from . import checks  # noqa: F401
        from . import site_config
        from . import widgets  # noqa: F401

        site_config.ready()
