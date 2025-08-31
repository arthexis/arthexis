from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = _("Business Models")

    def ready(self):  # pragma: no cover - called by Django
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate
        from .user_data import (
            patch_admin_user_datum,
            patch_admin_user_data_views,
        )

        def create_default_admin(**kwargs):
            User = get_user_model()
            if not User.objects.exists():
                User.objects.create_superuser("admin", password="admin")

        post_migrate.connect(create_default_admin, sender=self)
        patch_admin_user_datum()
        patch_admin_user_data_views()

        from pathlib import Path
        from django.conf import settings
        try:  # pragma: no cover - optional dependency
            from django_celery_beat.models import IntervalSchedule, PeriodicTask
        except Exception:  # pragma: no cover - optional dependency
            IntervalSchedule = PeriodicTask = None

        if PeriodicTask:
            lock = Path(settings.BASE_DIR) / "locks" / "celery.lck"
            if lock.exists():
                from django.db.utils import OperationalError

                try:
                    schedule, _ = IntervalSchedule.objects.get_or_create(
                        every=1, period=IntervalSchedule.HOURS
                    )
                    PeriodicTask.objects.get_or_create(
                        name="poll_email_collectors",
                        defaults={
                            "interval": schedule,
                            "task": "core.tasks.poll_email_collectors",
                        },
                    )
                except OperationalError:  # pragma: no cover - tables not ready
                    pass
