"""Regression coverage for retired sponsors runtime cleanup migration."""

from __future__ import annotations

from importlib import import_module

migration = import_module("apps.sponsors.migrations.0002_retire_runtime_models")


class StubQuerySet:
    """Minimal queryset stub that records delete invocations."""

    def __init__(self, first_result=None) -> None:
        """Initialize the queryset stub.

        Parameters:
            first_result (object | None): Value returned by ``first()``.

        Returns:
            None.
        """

        self.deleted = False
        self.first_result = first_result

    def delete(self) -> None:
        """Record that deletion was requested.

        Returns:
            None.
        """

        self.deleted = True

    def first(self):
        """Return the configured first-result value.

        Returns:
            object | None: Configured first-result value.
        """

        return self.first_result


class StubPeriodicTaskManager:
    """Periodic task manager stub for migration helper coverage."""

    def __init__(self) -> None:
        """Initialize captured task filters and upserts.

        Returns:
            None.
        """

        self.name_filters: list[str] = []
        self.task_filters: list[str] = []
        self.updated_task = None
        self.updated_defaults = None
        self._queryset = StubQuerySet()

    def filter(self, **kwargs):
        """Capture periodic task filters.

        Parameters:
            **kwargs: ORM-style filter keyword arguments.

        Returns:
            StubQuerySet: Stub queryset used for delete chaining.
        """

        if "name" in kwargs:
            self.name_filters.append(kwargs["name"])
        if "task" in kwargs:
            self.task_filters.append(kwargs["task"])
        return self._queryset

    def update_or_create(self, *, name, defaults):
        """Record periodic task recreation details.

        Parameters:
            name (str): Periodic task name.
            defaults (dict): Fields used for creation/update.

        Returns:
            tuple[object, bool]: Stub instance and created flag.
        """

        self.updated_task = name
        self.updated_defaults = defaults
        return object(), True


class StubIntervalScheduleManager:
    """Interval schedule manager stub for migration helper coverage."""

    def __init__(self) -> None:
        """Initialize captured get-or-create arguments.

        Returns:
            None.
        """

        self.calls: list[dict[str, object]] = []

    def get_or_create(self, **kwargs):
        """Record schedule creation parameters.

        Parameters:
            **kwargs: ORM-style creation fields.

        Returns:
            tuple[dict[str, object], bool]: Stub schedule and created flag.
        """

        self.calls.append(kwargs)
        return kwargs, True


class StubContentTypeManager:
    """Content type manager stub for cleanup and restore coverage."""

    def __init__(self, content_types=None) -> None:
        """Initialize the manager with known content types.

        Parameters:
            content_types (dict[str, StubContentType] | None): Existing content types.

        Returns:
            None.
        """

        self.content_types = content_types or {}
        self.filter_calls: list[tuple[str, str]] = []
        self.created: list[tuple[str, str]] = []

    def filter(self, **kwargs):
        """Return a queryset stub for the requested content type.

        Parameters:
            **kwargs: ORM-style filter keyword arguments.

        Returns:
            StubQuerySet: Queryset with the matching content type, if any.
        """

        key = (kwargs["app_label"], kwargs["model"])
        self.filter_calls.append(key)
        return StubQuerySet(self.content_types.get(key))

    def get_or_create(self, **kwargs):
        """Record content type recreation parameters.

        Parameters:
            **kwargs: ORM-style creation fields.

        Returns:
            tuple[StubContentType, bool]: Stub content type and created flag.
        """

        key = (kwargs["app_label"], kwargs["model"])
        self.created.append(key)
        return self.content_types.setdefault(key, StubContentType(key[0], key[1])), True


class StubPermissionManager:
    """Permission manager stub for cleanup and restore coverage."""

    def __init__(self) -> None:
        """Initialize captured permission operations.

        Returns:
            None.
        """

        self.deleted_content_types: list[object] = []
        self.created_permissions: list[tuple[object, str, str]] = []

    def filter(self, **kwargs):
        """Return a queryset stub recording the permission deletion target.

        Parameters:
            **kwargs: ORM-style filter keyword arguments.

        Returns:
            StubQuerySet: Queryset stub with delete tracking.
        """

        self.deleted_content_types.append(kwargs["content_type"])
        return StubQuerySet()

    def get_or_create(self, *, content_type, codename, defaults):
        """Record permission recreation parameters.

        Parameters:
            content_type (object): Permission content type.
            codename (str): Permission codename.
            defaults (dict[str, str]): Permission defaults.

        Returns:
            tuple[object, bool]: Stub permission and created flag.
        """

        self.created_permissions.append((content_type, codename, defaults["name"]))
        return object(), True


class StubPeriodicTask:
    """Historical periodic task model stub."""

    objects = StubPeriodicTaskManager()


class StubIntervalSchedule:
    """Historical interval schedule model stub."""

    objects = StubIntervalScheduleManager()


class StubContentType:
    """Historical content type model stub."""

    def __init__(self, app_label: str, model: str) -> None:
        """Initialize a content type stub.

        Parameters:
            app_label (str): Django app label.
            model (str): Lowercase model name.

        Returns:
            None.
        """

        self.app_label = app_label
        self.model = model
        self.deleted = False

    def delete(self) -> None:
        """Record that the content type was deleted.

        Returns:
            None.
        """

        self.deleted = True


class StubPermission:
    """Historical permission model stub."""

    objects = StubPermissionManager()


class StubApps:
    """Historical app registry stub for sponsors retirement migration tests."""

    def __init__(
        self, *, include_celery: bool = True, content_type_manager=None
    ) -> None:
        """Initialize the registry stub.

        Parameters:
            include_celery (bool): Whether Celery beat models are available.
            content_type_manager (StubContentTypeManager | None): Content type manager to expose.

        Returns:
            None.
        """

        self.include_celery = include_celery
        self.content_type_manager = content_type_manager or StubContentTypeManager()

    def get_model(self, app_label: str, model_name: str):
        """Return the requested historical model stub.

        Parameters:
            app_label (str): Django app label.
            model_name (str): Historical model name.

        Returns:
            type: Matching stub model.

        Raises:
            LookupError: If the requested model is intentionally unavailable.
        """

        if (app_label, model_name) == ("django_celery_beat", "PeriodicTask"):
            if not self.include_celery:
                raise LookupError("django_celery_beat unavailable")
            return StubPeriodicTask
        if (app_label, model_name) == ("django_celery_beat", "IntervalSchedule"):
            if not self.include_celery:
                raise LookupError("django_celery_beat unavailable")
            return StubIntervalSchedule
        if (app_label, model_name) == ("contenttypes", "ContentType"):

            class ContentTypeModel:
                objects = self.content_type_manager

            return ContentTypeModel
        if (app_label, model_name) == ("auth", "Permission"):
            return StubPermission
        raise AssertionError((app_label, model_name))


def test_delete_retired_sponsor_task_removes_legacy_beat_entries() -> None:
    """Regression: retirement should delete legacy sponsor renewal beat rows."""

    manager = StubPeriodicTaskManager()
    StubPeriodicTask.objects = manager

    migration.delete_retired_sponsor_task(StubApps(), schema_editor=None)

    assert manager.name_filters == [migration.SPONSOR_RENEWAL_TASK_NAME]
    assert manager.task_filters == [migration.SPONSOR_RENEWAL_TASK_PATH]
    assert manager._queryset.deleted is True


def test_restore_retired_sponsor_task_recreates_legacy_beat_entry() -> None:
    """Rollback should recreate the sponsor renewal beat row."""

    interval_manager = StubIntervalScheduleManager()
    periodic_manager = StubPeriodicTaskManager()
    StubIntervalSchedule.objects = interval_manager
    StubPeriodicTask.objects = periodic_manager

    migration.restore_retired_sponsor_task(StubApps(), schema_editor=None)

    assert interval_manager.calls == [{"every": 1, "period": "hours"}]
    assert periodic_manager.updated_task == migration.SPONSOR_RENEWAL_TASK_NAME
    assert periodic_manager.updated_defaults == {
        "interval": {"every": 1, "period": "hours"},
        "task": migration.SPONSOR_RENEWAL_TASK_PATH,
    }


def test_delete_retired_sponsor_content_types_removes_permissions_and_types() -> None:
    """Regression: retirement should drop dead sponsor auth metadata."""

    content_types = {
        ("sponsors", model_name): StubContentType("sponsors", model_name)
        for model_name in migration.SPONSOR_PERMISSION_NAMES
    }
    content_type_manager = StubContentTypeManager(content_types)
    permission_manager = StubPermissionManager()
    StubPermission.objects = permission_manager

    migration.delete_retired_sponsor_content_types(
        StubApps(content_type_manager=content_type_manager),
        schema_editor=None,
    )

    assert content_type_manager.filter_calls == [
        ("sponsors", model_name) for model_name in migration.SPONSOR_PERMISSION_NAMES
    ]
    assert permission_manager.deleted_content_types == list(content_types.values())
    assert all(content_type.deleted for content_type in content_types.values())


def test_restore_retired_sponsor_content_types_recreates_permissions() -> None:
    """Rollback should restore sponsor content types and auth permissions."""

    content_type_manager = StubContentTypeManager()
    permission_manager = StubPermissionManager()
    StubPermission.objects = permission_manager

    migration.restore_retired_sponsor_content_types(
        StubApps(content_type_manager=content_type_manager),
        schema_editor=None,
    )

    assert content_type_manager.created == [
        ("sponsors", model_name) for model_name in migration.SPONSOR_PERMISSION_NAMES
    ]
    assert len(permission_manager.created_permissions) == sum(
        len(permission_names)
        for permission_names in migration.SPONSOR_PERMISSION_NAMES.values()
    )
