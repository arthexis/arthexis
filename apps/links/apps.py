from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class LinksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.links"
    label = "links"
    order = 5
    verbose_name = _("Links")

    def ready(self):  # pragma: no cover - import for side effects
        from .reference_validation import ensure_reference_validation_task

        lock = Path(settings.BASE_DIR) / ".locks" / "celery.lck"
        if not lock.exists():
            return

        post_migrate.connect(ensure_reference_validation_task, sender=self)

        validation_dispatch_uid = "apps.links.apps.ensure_reference_validation_task"

        def ensure_reference_validation_on_connection(**kwargs):
            if not apps.ready:
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
