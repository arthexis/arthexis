import logging

from django.apps import AppConfig, apps as django_apps
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate

from apps.celery.utils import is_celery_enabled

logger = logging.getLogger(__name__)


class LinksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.links"
    label = "links"

    def ready(self):  # pragma: no cover - import for side effects
        if not is_celery_enabled():
            return

        from .reference_validation import ensure_reference_validation_task

        post_migrate.connect(
            ensure_reference_validation_task,
            sender=self,
            dispatch_uid="links_reference_validation_post_migrate",
            weak=False,
        )

        validation_dispatch_uid = "apps.links.apps.ensure_reference_validation_task"

        def ensure_reference_validation_on_connection(**kwargs):
            if not django_apps.ready:
                return
            connection = kwargs.get("connection")
            if connection is not None and connection.alias != "default":
                return
            try:
                ensure_reference_validation_task()
            finally:
                connection_created.disconnect(
                    receiver=ensure_reference_validation_on_connection,
                    dispatch_uid=validation_dispatch_uid,
                )

        connection_created.connect(
            ensure_reference_validation_on_connection,
            dispatch_uid=validation_dispatch_uid,
            weak=False,
        )
