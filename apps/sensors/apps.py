from django.apps import AppConfig
from django.db.models.signals import post_migrate

from apps.celery.utils import is_celery_enabled


class SensorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sensors"
    label = "sensors"

    def ready(self):  # pragma: no cover - import for side effects
        if not is_celery_enabled():
            return

        from .scheduling import ensure_thermometer_sampling_task

        post_migrate.connect(
            ensure_thermometer_sampling_task,
            sender=self,
            dispatch_uid="sensors_thermometer_sampling_post_migrate",
            weak=False,
        )
