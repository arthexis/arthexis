import types

from core import system


class _DummySchedule:
    def __str__(self):
        return "0 0 * * *"


class _DummyTask:
    enabled = True
    one_off = False
    queue = "celery"
    description = "Test task"
    task = "core.tasks.auto_upgrade"
    name = system.AUTO_UPGRADE_TASK_NAME
    total_run_count = 7
    start_time = None
    last_run_at = None
    expires = None
    interval_id = None
    crontab_id = None
    solar_id = None
    clocked_id = None
    pk = None

    @property
    def schedule(self):
        return _DummySchedule()


def test_auto_upgrade_run_count_resets_on_failure(monkeypatch):
    dummy_task = _DummyTask()

    monkeypatch.setattr(
        system, "_get_auto_upgrade_periodic_task", lambda: (dummy_task, True, "")
    )
    monkeypatch.setattr(system, "_read_auto_upgrade_failure_count", lambda base: 2)

    schedule_info = system._load_auto_upgrade_schedule()

    assert schedule_info["failure_count"] == 2
    assert schedule_info["total_run_count"] == 0


def test_auto_upgrade_run_count_preserved_without_failures(monkeypatch):
    dummy_task = _DummyTask()
    dummy_task.total_run_count = 4

    monkeypatch.setattr(
        system, "_get_auto_upgrade_periodic_task", lambda: (dummy_task, True, "")
    )
    monkeypatch.setattr(system, "_read_auto_upgrade_failure_count", lambda base: 0)

    schedule_info = system._load_auto_upgrade_schedule()

    assert schedule_info["failure_count"] == 0
    assert schedule_info["total_run_count"] == 4
