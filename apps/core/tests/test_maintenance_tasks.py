"""Task registration and compatibility coverage for core maintenance tasks."""

from __future__ import annotations

import importlib

from celery import current_app

migrate_maintenance_task_names_module = importlib.import_module(
    "apps.core.migrations.0004_migrate_maintenance_task_names"
)


def test_maintenance_tasks_register_only_canonical_names():
    """Maintenance tasks should be registered only under canonical task names."""

    from apps.core.tasks import maintenance as _maintenance

    del _maintenance

    registered_task_names = set(current_app.tasks.keys())

    assert "apps.core.tasks.maintenance._poll_emails" in registered_task_names
    assert "apps.core.tasks.maintenance._run_scheduled_release" in registered_task_names
    assert (
        "apps.core.tasks.maintenance._run_client_report_schedule"
        in registered_task_names
    )
    assert (
        "apps.core.tasks.maintenance._run_release_data_transform"
        in registered_task_names
    )

    assert "apps.core.tasks.poll_emails" not in registered_task_names
    assert "apps.core.tasks.run_scheduled_release" not in registered_task_names
    assert "apps.core.tasks.run_client_report_schedule" not in registered_task_names
    assert "apps.core.tasks.run_release_data_transform" not in registered_task_names

    assert "apps.core.tasks.maintenance.poll_emails" not in registered_task_names
    assert "apps.core.tasks.maintenance.run_scheduled_release" not in registered_task_names
    assert (
        "apps.core.tasks.maintenance.run_client_report_schedule"
        not in registered_task_names
    )
    assert (
        "apps.core.tasks.maintenance.run_release_data_transform"
        not in registered_task_names
    )


class _FakeQuerySet:
    def __init__(self, updates_by_task, task):
        self._task = task
        self._updates_by_task = updates_by_task

    def update(self, **kwargs):
        del kwargs
        return self._updates_by_task.get(self._task, 0)


class _FakePeriodicTaskManager:
    def __init__(self, updates_by_task):
        self._updates_by_task = updates_by_task

    def filter(self, **kwargs):
        return _FakeQuerySet(self._updates_by_task, kwargs["task"])


class _FakePeriodicTasksManager:
    def __init__(self):
        self.calls = []

    def update_or_create(self, **kwargs):
        self.calls.append(kwargs)
        return object(), True


class _FakeModelRegistry:
    def __init__(self, updates_by_task):
        self.periodic_tasks_manager = _FakePeriodicTasksManager()
        self.periodic_task_model = type(
            "PeriodicTask",
            (),
            {"objects": _FakePeriodicTaskManager(updates_by_task)},
        )
        self.periodic_tasks_model = type(
            "PeriodicTasks",
            (),
            {"objects": self.periodic_tasks_manager},
        )

    def get_model(self, app_label, model_name):
        model_map = {
            ("django_celery_beat", "PeriodicTask"): self.periodic_task_model,
            ("django_celery_beat", "PeriodicTasks"): self.periodic_tasks_model,
        }
        return model_map[(app_label, model_name)]


def test_migration_updates_periodic_tasks_change_tracker():
    fake_apps = _FakeModelRegistry(
        {"apps.core.tasks.poll_emails": 1},
    )

    migrate_maintenance_task_names_module.migrate_maintenance_task_names(
        fake_apps, schema_editor=None
    )

    assert len(fake_apps.periodic_tasks_manager.calls) == 1
    change_tracker_call = fake_apps.periodic_tasks_manager.calls[0]
    assert change_tracker_call["ident"] == 1
    assert "last_update" in change_tracker_call["defaults"]


def test_migration_skips_change_tracker_when_no_rows_are_updated():
    fake_apps = _FakeModelRegistry({})

    migrate_maintenance_task_names_module.migrate_maintenance_task_names(
        fake_apps, schema_editor=None
    )

    assert fake_apps.periodic_tasks_manager.calls == []
