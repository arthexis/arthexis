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
        import types
        from pathlib import Path

        from django.conf import settings
        from django.core.exceptions import ObjectDoesNotExist, ValidationError
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate, pre_save
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
        from .celery_utils import normalize_periodic_task_name
        from .admin_history import patch_admin_history

        from django_otp.plugins.otp_totp.models import TOTPDevice as OTP_TOTPDevice

        try:
            from django_celery_beat.models import CrontabSchedule, PeriodicTask
        except Exception:  # pragma: no cover - optional dependency
            CrontabSchedule = None
            PeriodicTask = None
        else:
            if not hasattr(CrontabSchedule, "natural_key"):
                def _core_crontab_natural_key(self):
                    return (
                        self.minute,
                        self.hour,
                        self.day_of_week,
                        self.day_of_month,
                        self.month_of_year,
                        str(self.timezone),
                    )

                CrontabSchedule.natural_key = _core_crontab_natural_key

            if (
                CrontabSchedule is not None
                and not hasattr(CrontabSchedule.objects, "get_by_natural_key")
            ):
                def _core_crontab_get_by_natural_key(
                    manager,
                    minute,
                    hour,
                    day_of_week,
                    day_of_month,
                    month_of_year,
                    timezone,
                ):
                    return manager.get(
                        minute=minute,
                        hour=hour,
                        day_of_week=day_of_week,
                        day_of_month=day_of_month,
                        month_of_year=month_of_year,
                        timezone=timezone,
                    )

                manager = CrontabSchedule.objects
                manager.get_by_natural_key = types.MethodType(
                    _core_crontab_get_by_natural_key, manager
                )
                default_manager = CrontabSchedule._default_manager
                if default_manager is not manager:
                    default_manager.get_by_natural_key = types.MethodType(
                        _core_crontab_get_by_natural_key, default_manager
                    )
                base_manager = getattr(CrontabSchedule, "_base_manager", None)
                if base_manager and base_manager not in {manager, default_manager}:
                    base_manager.get_by_natural_key = types.MethodType(
                        _core_crontab_get_by_natural_key, base_manager
                    )

            if (
                PeriodicTask is not None
                and not getattr(PeriodicTask, "_core_fixture_upsert", False)
            ):
                def _core_periodic_task_pre_save(sender, instance, **kwargs):
                    manager = sender.objects
                    slug = normalize_periodic_task_name(manager, instance.name)
                    instance.name = slug
                    if instance.pk:
                        return
                    existing_pk = (
                        manager.filter(name=slug)
                        .values_list("pk", flat=True)
                        .first()
                    )
                    if existing_pk:
                        instance.pk = existing_pk
                        instance._state.adding = False

                pre_save.connect(
                    _core_periodic_task_pre_save,
                    sender=PeriodicTask,
                    dispatch_uid="core_periodic_task_fixture_pre_save",
                    weak=False,
                )
                PeriodicTask._core_fixture_upsert = True
                PeriodicTask._core_fixture_pre_save_handler = (
                    _core_periodic_task_pre_save
                )

                if not getattr(
                    PeriodicTask, "_core_fixture_validate_patch", False
                ):
                    original_validate_unique = PeriodicTask.validate_unique

                    def _core_periodic_task_validate_unique(self, *args, **kwargs):
                        try:
                            return original_validate_unique(self, *args, **kwargs)
                        except ValidationError as exc:
                            error_dict = getattr(exc, "error_dict", None) or {}
                            if "name" not in error_dict or self.pk:
                                raise
                            manager = type(self).objects
                            slug = normalize_periodic_task_name(manager, self.name)
                            existing = manager.filter(name=slug).first()
                            if not existing:
                                raise
                            self.pk = existing.pk
                            self._state.adding = False
                            self.name = slug
                            return original_validate_unique(self, *args, **kwargs)

                    PeriodicTask.validate_unique = _core_periodic_task_validate_unique
                    PeriodicTask._core_fixture_validate_patch = True
                    PeriodicTask._core_fixture_validate_unique = (
                        original_validate_unique
                    )

        if not hasattr(
            OTP_TOTPDevice._read_str_from_settings, "_core_totp_issuer_patch"
        ):
            original_read_str = OTP_TOTPDevice._read_str_from_settings

            def _core_totp_read_str(self, key):
                if key == "OTP_TOTP_ISSUER":
                    try:
                        settings_obj = self.custom_settings
                    except ObjectDoesNotExist:
                        settings_obj = None
                    if settings_obj and settings_obj.issuer:
                        return settings_obj.issuer
                return original_read_str(self, key)

            _core_totp_read_str._core_totp_issuer_patch = True
            OTP_TOTPDevice._read_str_from_settings = _core_totp_read_str

        if not getattr(OTP_TOTPDevice, "_core_user_datum_patch", False):
            from .models import TOTPDeviceSettings

            def _totp_should_persist(settings_obj):
                return bool(
                    settings_obj
                    and (
                        settings_obj.issuer
                        or settings_obj.is_seed_data
                        or settings_obj.is_user_data
                    )
                )

            def _totp_save_or_delete(settings_obj):
                if settings_obj is None:
                    return
                if _totp_should_persist(settings_obj):
                    if settings_obj.pk:
                        settings_obj.save(
                            update_fields=["issuer", "is_seed_data", "is_user_data"]
                        )
                    else:
                        settings_obj.save()
                elif settings_obj.pk:
                    settings_obj.delete()

            def _totp_get_flag(instance, attr):
                cache_key = f"_{attr}"
                if cache_key in instance.__dict__:
                    return instance.__dict__[cache_key]
                try:
                    settings_obj = instance.custom_settings
                except ObjectDoesNotExist:
                    value = False
                else:
                    value = bool(getattr(settings_obj, attr, False))
                instance.__dict__[cache_key] = value
                return value

            def _totp_set_flag(instance, attr, value):
                cache_key = f"_{attr}"
                value = bool(value)
                try:
                    settings_obj = instance.custom_settings
                except ObjectDoesNotExist:
                    if not value:
                        instance.__dict__[cache_key] = False
                        return
                    settings_obj = TOTPDeviceSettings(device=instance)
                setattr(settings_obj, attr, value)
                _totp_save_or_delete(settings_obj)
                instance.__dict__[cache_key] = value

            def _totp_get_user_data(instance):
                return _totp_get_flag(instance, "is_user_data")

            def _totp_set_user_data(instance, value):
                _totp_set_flag(instance, "is_user_data", value)

            def _totp_get_seed_data(instance):
                return _totp_get_flag(instance, "is_seed_data")

            def _totp_set_seed_data(instance, value):
                _totp_set_flag(instance, "is_seed_data", value)

            OTP_TOTPDevice.is_user_data = property(
                _totp_get_user_data, _totp_set_user_data
            )
            OTP_TOTPDevice.is_seed_data = property(
                _totp_get_seed_data, _totp_set_seed_data
            )
            if not hasattr(OTP_TOTPDevice, "all_objects"):
                OTP_TOTPDevice.all_objects = OTP_TOTPDevice._default_manager
            OTP_TOTPDevice.supports_user_datum = True
            OTP_TOTPDevice.supports_seed_datum = True
            OTP_TOTPDevice._core_user_datum_patch = True

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
            from django.db import DEFAULT_DB_ALIAS, connections

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
                    task_name = normalize_periodic_task_name(
                        PeriodicTask.objects, "poll_email_collectors"
                    )
                    PeriodicTask.objects.update_or_create(
                        name=task_name,
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

            default_connection = connections[DEFAULT_DB_ALIAS]
            if default_connection.connection is not None:
                ensure_auto_upgrade_on_connection(connection=default_connection)

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

