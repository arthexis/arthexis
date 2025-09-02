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
        from .system import patch_admin_system_view
        from .environment import patch_admin_environment_view

        def create_default_arthexis(**kwargs):
            User = get_user_model()
            if not User.all_objects.exists():
                User.all_objects.create_superuser(
                    pk=1,
                    username="arthexis",
                    email="arthexis@gmail.com",
                    password="arthexis",
                )

        post_migrate.connect(create_default_arthexis, sender=self)
        patch_admin_user_datum()
        patch_admin_user_data_views()
        patch_admin_system_view()
        patch_admin_environment_view()

        from pathlib import Path
        from django.conf import settings

        lock = Path(settings.BASE_DIR) / "locks" / "celery.lck"

        if lock.exists():

            def ensure_email_collector_task(**kwargs):
                try:  # pragma: no cover - optional dependency
                    from django_celery_beat.models import (
                        IntervalSchedule,
                        PeriodicTask,
                    )
                    from django.db.utils import OperationalError, ProgrammingError
                except Exception:  # pragma: no cover - tables or module not ready
                    return

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
                except (OperationalError, ProgrammingError):
                    pass

            post_migrate.connect(ensure_email_collector_task, sender=self)
