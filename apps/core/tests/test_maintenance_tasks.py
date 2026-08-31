"""Task registration and compatibility coverage for core maintenance tasks."""

from __future__ import annotations

from celery import current_app


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
