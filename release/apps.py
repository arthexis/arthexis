from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ReleaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "release"

    def ready(self) -> None:  # pragma: no cover - startup hook
        from .models import load_seeddata_fixture

        post_migrate.connect(load_seeddata_fixture, sender=self)
