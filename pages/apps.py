from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pages"
    verbose_name = "7. Experience"

    def ready(self):  # pragma: no cover - import for side effects
        from . import checks  # noqa: F401
        from . import site_config

        site_config.ready()
