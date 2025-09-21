from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pages"
    verbose_name = "7. Experience"

    def ready(self):  # pragma: no cover - import for side effects
        super().ready()

        from . import checks  # noqa: F401
        from .site_badge_defaults import ensure_site_default_badge_color_field

        ensure_site_default_badge_color_field()
