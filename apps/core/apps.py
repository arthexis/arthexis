import logging

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):  # pragma: no cover - called by Django
        _setup_celery_beat_integrations()
        _register_admin_and_post_migrate_handlers(self)
        _patch_entity_deserialization()
        _configure_lock_dependent_tasks(self)
        _connect_sqlite_wal()
        _enable_usage_analytics()


def _setup_celery_beat_integrations():
    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except Exception:  # pragma: no cover - optional dependency
        return

    import types

    from django.core.exceptions import ValidationError
    from django.db.models.signals import pre_save

    from apps.celery.utils import normalize_periodic_task_name, periodic_task_name_variants

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

    if not hasattr(CrontabSchedule.objects, "get_by_natural_key"):

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

    if getattr(PeriodicTask, "_core_fixture_upsert", False):
        return

    def _core_periodic_task_pre_save(sender, instance, **kwargs):
        manager = sender.objects
        original_name = instance.name
        slug = normalize_periodic_task_name(manager, original_name)

        if instance.pk:
            existing_pk_row = manager.filter(pk=instance.pk).first()
            if existing_pk_row and original_name not in periodic_task_name_variants(
                existing_pk_row.name
            ):
                instance.pk = None
                instance._state.adding = True
            return

        if original_name == slug:
            existing_pk = (
                manager.filter(name__in=periodic_task_name_variants(slug))
                .values_list("pk", flat=True)
                .first()
            )
            if existing_pk:
                instance.pk = existing_pk
                instance._state.adding = False
                instance._core_force_update = True

    pre_save.connect(
        _core_periodic_task_pre_save,
        sender=PeriodicTask,
        dispatch_uid="core_periodic_task_fixture_pre_save",
        weak=False,
    )
    PeriodicTask._core_fixture_upsert = True
    PeriodicTask._core_fixture_pre_save_handler = _core_periodic_task_pre_save

    if getattr(PeriodicTask, "_core_fixture_validate_patch", False):
        return

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
    PeriodicTask._core_fixture_validate_unique = original_validate_unique

    if getattr(PeriodicTask, "_core_fixture_save_patch", False):
        return

    original_save = PeriodicTask.save

    def _core_periodic_task_save(self, *args, **kwargs):
        force_insert = kwargs.pop("force_insert", False)
        force_update = kwargs.pop("force_update", False)

        manager = type(self).objects
        original_name = self.name
        slug = normalize_periodic_task_name(manager, original_name)

        if getattr(self, "_core_normalizing", False):
            return original_save(
                self,
                *args,
                force_insert=force_insert,
                force_update=force_update,
                **kwargs,
            )

        if getattr(self, "_core_force_update", False):
            force_insert = False
            force_update = True

        if self.pk:
            existing_pk_row = manager.filter(pk=self.pk).first()
            if existing_pk_row and existing_pk_row.name not in periodic_task_name_variants(
                original_name
            ):
                self.pk = None
                self._state.adding = True
                force_insert = False

        if original_name == slug:
            existing_pk = (
                manager.filter(name__in=periodic_task_name_variants(slug))
                .exclude(pk=self.pk)
                .values_list("pk", flat=True)
                .first()
            )
            if existing_pk and not self.pk:
                self.pk = existing_pk
                self._state.adding = False
                force_insert = False
                force_update = True
            self.name = slug
        else:
            existing_slug_pk = (
                manager.filter(name=slug)
                .exclude(pk=self.pk)
                .values_list("pk", flat=True)
                .first()
            )
            if existing_slug_pk:
                if not self.pk or self.pk != existing_slug_pk:
                    self.pk = existing_slug_pk
                    self._state.adding = False
                    force_insert = False
                    force_update = True
                self.name = slug
            else:
                self.name = original_name

        saved = original_save(
            self,
            *args,
            force_insert=force_insert,
            force_update=force_update,
            **kwargs,
        )

        if not getattr(self, "_core_normalizing", False):
            normalize_periodic_task_name(manager, original_name)

        return saved

    PeriodicTask.save = _core_periodic_task_save
    PeriodicTask._core_fixture_save_patch = True
    PeriodicTask._core_fixture_original_save = original_save


def _register_admin_and_post_migrate_handlers(config):
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_migrate

    from .admin_history import patch_admin_history
    from .environment import patch_admin_environment_view
    from .system import patch_admin_system_view

    def create_default_arthexis(**kwargs):
        User = get_user_model()
        if not User.all_objects.exists():
            user = User.all_objects.create_superuser(
                pk=1,
                username="arthexis",
                email="arthexis@gmail.com",
                password="arthexis",
            )
            from apps.locals.models import ensure_admin_favorites

            ensure_admin_favorites(user)

    post_migrate.connect(create_default_arthexis, sender=config)

    patch_admin_system_view()
    patch_admin_environment_view()
    patch_admin_history()


def _patch_entity_deserialization():
    from functools import wraps

    from django.core.serializers import base as serializer_base

    from .entity import Entity

    if hasattr(serializer_base.DeserializedObject.save, "_entity_fixture_patch"):
        return

    original_save = serializer_base.DeserializedObject.save

    @wraps(original_save)
    def patched_save(self, save_m2m=True, using=None, **kwargs):
        obj = self.object
        if isinstance(obj, Entity):
            manager = getattr(type(obj), "all_objects", type(obj)._default_manager)
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
                try:
                    existing = (
                        manager.filter(**lookup)
                        .only("pk", "is_seed_data", "is_user_data")
                        .first()
                    )
                except Exception as exc:  # pragma: no cover - db not ready
                    from django.db.utils import OperationalError, ProgrammingError

                    if isinstance(exc, (OperationalError, ProgrammingError)):
                        existing = None
                    else:
                        raise
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


def _configure_lock_dependent_tasks(config):
    from django.db.backends.signals import connection_created
    from django.db.models.signals import post_migrate

    from apps.celery.utils import is_celery_enabled

    if not is_celery_enabled():
        return

    from .auto_upgrade import ensure_auto_upgrade_periodic_task

    def ensure_email_collector_task(**kwargs):
        try:  # pragma: no cover - optional dependency
            from django_celery_beat.models import IntervalSchedule, PeriodicTask
            from django.db.utils import OperationalError, ProgrammingError
        except Exception:  # pragma: no cover - tables or module not ready
            return

        from apps.celery.utils import normalize_periodic_task_name

        try:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1, period=IntervalSchedule.HOURS
            )
            task_name = normalize_periodic_task_name(
                PeriodicTask.objects, "poll_emails"
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.core.tasks.poll_emails",
                },
            )
        except (OperationalError, ProgrammingError):
            pass

    post_migrate.connect(ensure_email_collector_task, sender=config)
    post_migrate.connect(ensure_auto_upgrade_periodic_task, sender=config)

    auto_upgrade_dispatch_uid = "apps.core.apps.ensure_auto_upgrade_periodic_task"

    from django.apps import apps

    def ensure_auto_upgrade_on_connection(**kwargs):
        if not apps.ready:
            return
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

def _connect_sqlite_wal():
    from django.db import connections
    from django.db.backends.signals import connection_created
    from django.apps import apps

    def enable_sqlite_wal(**kwargs):
        if not apps.ready:
            return
        connection = kwargs.get("connection")
        if connection and connection.vendor == "sqlite":
            from django.db import DatabaseError

            try:
                with connection.cursor() as cursor:
                    try:
                        cursor.execute("PRAGMA journal_mode=WAL;")
                        cursor.execute("PRAGMA busy_timeout=60000;")
                    except DatabaseError as exc:
                        logger.warning(
                            "SQLite WAL setup failed; falling back to DELETE journal mode: %s",
                            exc,
                        )
                        try:
                            cursor.execute("PRAGMA journal_mode=DELETE;")
                        except DatabaseError as fallback_exc:
                            logger.warning(
                                "SQLite DELETE journal mode fallback failed: %s",
                                fallback_exc,
                            )
            except DatabaseError as exc:
                logger.warning(
                    "Skipping SQLite WAL setup; unable to open cursor: %s",
                    exc,
                )

    connection_created.connect(
        enable_sqlite_wal,
        dispatch_uid="apps.core.enable_sqlite_wal",
        weak=False,
    )


def _enable_usage_analytics():
    from . import analytics  # noqa: F401 - ensure signal registration
