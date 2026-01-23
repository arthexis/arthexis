from django.apps import AppConfig, apps as django_apps
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate

from apps.celery.utils import is_celery_enabled


class VideoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.video"
    verbose_name = "Video"

    def ready(self):  # pragma: no cover - import for side effects
        if not is_celery_enabled():
            return

        from .thumbnail_schedule import ensure_mjpeg_thumbnail_task

        post_migrate.connect(
            ensure_mjpeg_thumbnail_task,
            sender=self,
            dispatch_uid="video_mjpeg_thumbnail_post_migrate",
            weak=False,
        )

        dispatch_uid = "apps.video.apps.ensure_mjpeg_thumbnail_task"

        def ensure_thumbnail_task_on_connection(**kwargs):
            if not django_apps.ready:
                return
            connection = kwargs.get("connection")
            if connection is not None and connection.alias != "default":
                return
            try:
                ensure_mjpeg_thumbnail_task()
            finally:
                connection_created.disconnect(
                    receiver=ensure_thumbnail_task_on_connection,
                    dispatch_uid=dispatch_uid,
                )

        connection_created.connect(
            ensure_thumbnail_task_on_connection,
            dispatch_uid=dispatch_uid,
            weak=False,
        )
