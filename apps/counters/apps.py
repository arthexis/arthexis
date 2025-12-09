from django.apps import AppConfig


class CountersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.counters"
    label = "counters"

    def ready(self):
        from django.db.models.signals import post_migrate

        from .defaults import ensure_default_badge_counters

        post_migrate.connect(
            lambda **_kwargs: ensure_default_badge_counters(), sender=self
        )
