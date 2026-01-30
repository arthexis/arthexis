"""App config for sponsors."""

from django.apps import AppConfig, apps as django_apps
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate

from apps.celery.utils import is_celery_enabled


class SponsorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sponsors"
    verbose_name = "Sponsors"

    def ready(self):  # pragma: no cover - import for side effects
        if not is_celery_enabled():
            return

        from .renewal_schedule import ensure_sponsor_renewal_task

        post_migrate.connect(
            ensure_sponsor_renewal_task,
            sender=self,
            dispatch_uid="sponsors_post_migrate_renewal_schedule",
            weak=False,
        )

        dispatch_uid = "apps.sponsors.apps.ensure_sponsor_renewal_task"

        def ensure_task_on_connection(**kwargs):
            if not django_apps.ready:
                return
            connection = kwargs.get("connection")
            if connection is not None and connection.alias != "default":
                return
            try:
                ensure_sponsor_renewal_task()
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
