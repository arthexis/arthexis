"""Task registration and compatibility coverage for core maintenance tasks."""

from __future__ import annotations

from celery import current_app


def test_maintenance_tasks_register_canonical_and_compatibility_names():
    """Maintenance tasks retain historical names during the compatibility window."""

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

    # Historical names remain registered for persisted django-celery-beat rows
    # and queued tasks throughout the core-boundary compatibility window.
    assert "apps.core.tasks.run_scheduled_release" in registered_task_names
    assert "apps.core.tasks.run_release_data_transform" in registered_task_names
