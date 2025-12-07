from django_celery_beat.apps import BeatConfig as BaseBeatConfig


class CeleryBeatConfig(BaseBeatConfig):
    """Customize Periodic Tasks admin behavior."""

    order = 5
    verbose_name = "Celery"

    def ready(self):  # pragma: no cover - exercised via tests
        super().ready()

        # Ensure Celery admin customizations and proxy registrations are loaded.
        from . import admin  # noqa: F401
