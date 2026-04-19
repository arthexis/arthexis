"""Application configuration for classification orchestration."""

from django.apps import AppConfig, apps as django_apps
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _

from apps.celery.utils import is_celery_enabled


class ClassificationConfig(AppConfig):
    """Register classification models and signals."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.classification"
    verbose_name = _("Classification")

    def ready(self) -> None:
        """Import signals and synchronize optional periodic tasks."""

        from . import signals  # noqa: F401

        if not is_celery_enabled():
            return

        from .scheduling import ensure_camera_classification_task

        post_migrate.connect(
            ensure_camera_classification_task,
            sender=self,
            dispatch_uid="classification_camera_loop_post_migrate",
            weak=False,
        )

        dispatch_uid = "apps.classification.apps.ensure_camera_classification_task"

        def ensure_task_on_connection(**kwargs):
            if not django_apps.ready:
                return
            connection = kwargs.get("connection")
            if connection is not None and connection.alias != "default":
                return
            try:
                ensure_camera_classification_task()
            finally:
                connection_created.disconnect(
                    receiver=ensure_task_on_connection,
                    dispatch_uid=dispatch_uid,
                )

        connection_created.connect(
            ensure_task_on_connection,
            dispatch_uid=dispatch_uid,
            weak=False,
        )
