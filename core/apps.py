import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = _("2. Business")

    def ready(self):  # pragma: no cover - called by Django
        from contextlib import suppress
        from functools import wraps
        import hashlib
        import time
        import traceback
        from pathlib import Path

        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate
        from django.core.signals import got_request_exception

        from core.github_helper import report_exception_to_github
        from .entity import Entity
        from .user_data import (
            patch_admin_user_datum,
            patch_admin_user_data_views,
        )
        from .system import patch_admin_system_view
        from .environment import patch_admin_environment_view
        from .sigil_builder import (
            patch_admin_sigil_builder_view,
            generate_model_sigils,
        )
        from .admin_history import patch_admin_history

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
        post_migrate.connect(generate_model_sigils, sender=self)
        patch_admin_user_datum()
        patch_admin_user_data_views()
        patch_admin_system_view()
        patch_admin_environment_view()
        patch_admin_sigil_builder_view()
        patch_admin_history()

        from django.core.serializers import base as serializer_base

        if not hasattr(
            serializer_base.DeserializedObject.save, "_entity_fixture_patch"
        ):
            original_save = serializer_base.DeserializedObject.save

            @wraps(original_save)
            def patched_save(self, save_m2m=True, using=None, **kwargs):
                obj = self.object
                if isinstance(obj, Entity):
                    manager = getattr(
                        type(obj), "all_objects", type(obj)._default_manager
                    )
                    if using:
                        manager = manager.db_manager(using)
                    for fields in obj._unique_field_groups():
                        lookup = {}
                        for field in fields:
                            value = getattr(obj, field.attname)
                            if value is None:
                                lookup = {}
                                break
                            lookup[field.attname] = value
                        if not lookup:
                            continue
                        existing = (
                            manager.filter(**lookup)
                            .only("pk", "is_seed_data", "is_user_data")
                            .first()
                        )
                        if existing is not None:
                            obj.pk = existing.pk
                            obj.is_seed_data = existing.is_seed_data
                            obj.is_user_data = existing.is_user_data
                            obj._state.adding = False
                            if using:
                                obj._state.db = using
                            break
                return original_save(self, save_m2m=save_m2m, using=using, **kwargs)

            patched_save._entity_fixture_patch = True
            serializer_base.DeserializedObject.save = patched_save

        lock = Path(settings.BASE_DIR) / "locks" / "celery.lck"

        from django.db.backends.signals import connection_created

        if lock.exists():
            from .auto_upgrade import ensure_auto_upgrade_periodic_task

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
            post_migrate.connect(ensure_auto_upgrade_periodic_task, sender=self)

            auto_upgrade_dispatch_uid = "core.apps.ensure_auto_upgrade_periodic_task"

            def ensure_auto_upgrade_on_connection(**kwargs):
                connection = kwargs.get("connection")
                if connection is not None and connection.alias != "default":
                    return

                try:
                    ensure_auto_upgrade_periodic_task()
                finally:
                    connection_created.disconnect(
                        receiver=ensure_auto_upgrade_on_connection,
                        dispatch_uid=auto_upgrade_dispatch_uid,
                    )

            connection_created.connect(
                ensure_auto_upgrade_on_connection,
                dispatch_uid=auto_upgrade_dispatch_uid,
                weak=False,
            )

        def enable_sqlite_wal(**kwargs):
            connection = kwargs.get("connection")
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=60000;")
                cursor.close()

        connection_created.connect(enable_sqlite_wal)

        def queue_github_issue(sender, request=None, **kwargs):
            if not getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True):
                return
            if request is None:
                return

            exception = kwargs.get("exception")
            if exception is None:
                return

            try:
                tb_exc = traceback.TracebackException.from_exception(exception)
                stack = tb_exc.stack
                top_frame = stack[-1] if stack else None
                fingerprint_parts = [
                    exception.__class__.__module__,
                    exception.__class__.__name__,
                ]
                if top_frame:
                    fingerprint_parts.extend(
                        [
                            top_frame.filename,
                            str(top_frame.lineno),
                            top_frame.name,
                        ]
                    )
                fingerprint = hashlib.sha256(
                    "|".join(fingerprint_parts).encode("utf-8")
                ).hexdigest()

                cooldown = getattr(settings, "GITHUB_ISSUE_REPORTING_COOLDOWN", 3600)
                lock_dir = Path(settings.BASE_DIR) / "locks" / "github-issues"
                fingerprint_path = None
                now = time.time()

                with suppress(OSError):
                    lock_dir.mkdir(parents=True, exist_ok=True)
                    fingerprint_path = lock_dir / fingerprint
                    if fingerprint_path.exists():
                        age = now - fingerprint_path.stat().st_mtime
                        if age < cooldown:
                            return

                if fingerprint_path is not None:
                    with suppress(OSError):
                        fingerprint_path.write_text(str(now))

                user_repr = None
                user = getattr(request, "user", None)
                if user is not None:
                    try:
                        if getattr(user, "is_authenticated", False):
                            user_repr = user.get_username()
                        else:
                            user_repr = "anonymous"
                    except Exception:  # pragma: no cover - defensive
                        user_repr = str(user)

                payload = {
                    "path": getattr(request, "path", None),
                    "method": getattr(request, "method", None),
                    "user": user_repr,
                    "active_app": getattr(request, "active_app", None),
                    "fingerprint": fingerprint,
                    "exception_class": f"{exception.__class__.__module__}.{exception.__class__.__name__}",
                    "traceback": "".join(tb_exc.format()),
                }

                report_exception_to_github.delay(payload)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to queue GitHub issue from request exception")

        got_request_exception.connect(
            queue_github_issue,
            dispatch_uid="core.github_issue_reporter",
            weak=False,
        )
