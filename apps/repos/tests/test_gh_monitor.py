from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from filelock import FileLock

from apps.features.models import Feature
from apps.repos import github_monitor
from apps.repos.models import GitHubMonitorItem, GitHubMonitorTask, GitHubRepository
from apps.skills.models import Skill


def _enable_monitor_feature() -> Feature:
    feature = Feature.objects.create(
        slug=github_monitor.GITHUB_MONITOR_FEATURE_SLUG,
        display="GitHub Monitoring",
        code_locations=["apps/repos/github_monitor.py"],
        is_enabled=True,
    )
    return feature


def _configure_defaults() -> None:
    github_monitor.configure_default_monitoring(repository="octo/demo", write=True)


def _issue(number: int, title: str, marker: str) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "body": f"{marker}\n\nFailure body",
        "state": "open",
        "html_url": f"https://github.example/issues/{number}",
    }


@pytest.mark.django_db
def test_configure_default_monitoring_writes_feature_tasks_and_policy_skills():
    result = github_monitor.configure_default_monitoring(
        repository="octo/demo",
        codex_command="codex --profile monitor",
        inactivity_timeout_minutes=12,
        write=True,
    )

    assert result["feature"]["enabled"] is True
    assert GitHubRepository.objects.get(owner="octo", name="demo")
    assert Feature.objects.get(
        slug=github_monitor.GITHUB_MONITOR_FEATURE_SLUG
    ).is_enabled

    tasks = {
        task.name: task
        for task in GitHubMonitorTask.objects.select_related("repository").all()
    }
    assert set(tasks) == {"install-health", "release-readiness"}
    assert tasks["install-health"].repository.slug == "octo/demo"
    assert tasks["install-health"].codex_command == "codex --profile monitor"
    assert tasks["install-health"].inactivity_timeout_minutes == 12
    assert tasks["install-health"].skill_slugs == list(
        github_monitor.DEFAULT_POLICY_SKILLS
    )

    assert set(
        Skill.objects.filter(slug__in=github_monitor.DEFAULT_POLICY_SKILLS).values_list(
            "slug", flat=True
        )
    ) == set(github_monitor.DEFAULT_POLICY_SKILLS)


@pytest.mark.django_db
def test_configure_default_monitoring_restores_soft_deleted_repository():
    repository = GitHubRepository.objects.create(
        owner="octo",
        name="demo",
        html_url="https://github.example/octo/demo",
    )
    GitHubRepository.all_objects.filter(pk=repository.pk).update(is_seed_data=True)
    repository = GitHubRepository.all_objects.get(pk=repository.pk)
    repository.delete()

    github_monitor.configure_default_monitoring(repository="octo/demo", write=True)

    repository = GitHubRepository.all_objects.get(pk=repository.pk)
    assert repository.is_deleted is False
    assert repository.html_url == "https://github.com/octo/demo"


@pytest.mark.django_db
def test_monitor_cycle_launches_only_one_terminal(tmp_path, monkeypatch):
    _enable_monitor_feature()
    _configure_defaults()
    monkeypatch.setattr(github_monitor, "desktop_ui_enabled", lambda: True)
    monkeypatch.setattr(
        github_monitor.github_service, "get_github_issue_token", lambda: "token"
    )
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)

    issues = [
        _issue(
            1, github_monitor.INSTALL_HEALTH_TITLE, github_monitor.INSTALL_HEALTH_MARKER
        ),
        _issue(
            2,
            github_monitor.RELEASE_READINESS_TITLE,
            github_monitor.RELEASE_READINESS_MARKER,
        ),
    ]
    monkeypatch.setattr(
        github_monitor.github_service,
        "fetch_repository_issues",
        lambda **_: list(issues),
    )
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda pid_file: True)

    launches: list[dict[str, object]] = []

    def fake_launch(command, *, title: str, state_key: str) -> Path:
        launches.append({"command": command, "title": title, "state_key": state_key})
        pid_file = tmp_path / f"{state_key}.pid"
        pid_file.write_text("123\nfake command\n", encoding="utf-8")
        return pid_file

    monkeypatch.setattr(github_monitor, "_launch_command", fake_launch)

    first_result = github_monitor.run_monitor_cycle()
    second_result = github_monitor.run_monitor_cycle()

    assert first_result["launch"]["reason"] == "launched"
    assert second_result["active"]["reason"] == "running"
    assert second_result["launch"]["reason"] == "launch_disabled"
    assert len(launches) == 1
    assert (
        GitHubMonitorItem.objects.filter(status=GitHubMonitorItem.Status.ACTIVE).count()
        == 1
    )
    assert (
        GitHubMonitorItem.objects.filter(status=GitHubMonitorItem.Status.QUEUED).count()
        == 1
    )
    assert (
        "gh_monitor heartbeat --item"
        in GitHubMonitorItem.objects.get(status=GitHubMonitorItem.Status.ACTIVE).prompt
    )


@pytest.mark.django_db
def test_sync_monitor_items_requeues_completed_issue_when_seen_open_again(monkeypatch):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    issue = _issue(
        42, github_monitor.INSTALL_HEALTH_TITLE, github_monitor.INSTALL_HEALTH_MARKER
    )
    first_seen = timezone.now()
    second_seen = first_seen + timedelta(minutes=30)
    monkeypatch.setattr(
        github_monitor.github_service,
        "fetch_repository_issues",
        lambda **_: [issue],
    )

    github_monitor.sync_monitor_items(token="token", now=first_seen)
    item = GitHubMonitorItem.objects.get(task=task, issue_number=42)
    item.prompt = "old prompt"
    item.terminal_pid_file = "old.pid"
    item.terminal_state_key = "old-state"
    item.launched_at = first_seen
    item.last_activity_at = first_seen
    item.save(
        update_fields=[
            "prompt",
            "terminal_pid_file",
            "terminal_state_key",
            "launched_at",
            "last_activity_at",
        ]
    )
    item.mark_status(GitHubMonitorItem.Status.COMPLETED)

    github_monitor.sync_monitor_items(token="token", now=second_seen)

    item.refresh_from_db()
    assert item.status == GitHubMonitorItem.Status.QUEUED
    assert item.queued_at == second_seen
    assert item.completed_at is None
    assert item.launched_at is None
    assert item.last_activity_at is None
    assert item.prompt == ""
    assert item.terminal_pid_file == ""
    assert item.terminal_state_key == ""


@pytest.mark.django_db
def test_sync_monitor_items_resolves_existing_row_after_repository_change(monkeypatch):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    issue = _issue(
        77, github_monitor.INSTALL_HEALTH_TITLE, github_monitor.INSTALL_HEALTH_MARKER
    )
    monkeypatch.setattr(
        github_monitor.github_service,
        "fetch_repository_issues",
        lambda **_: [issue],
    )

    github_monitor.sync_monitor_items(token="token", now=timezone.now())
    item = GitHubMonitorItem.objects.get(task=task, issue_number=77)
    old_fingerprint = item.fingerprint
    task.repository = GitHubRepository.objects.create(
        owner="octo",
        name="renamed",
        html_url="https://github.example/octo/renamed",
    )
    task.save(update_fields=["repository"])

    github_monitor.sync_monitor_items(token="token", now=timezone.now())

    item.refresh_from_db()
    assert GitHubMonitorItem.objects.filter(task=task, issue_number=77).count() == 1
    assert item.fingerprint != old_fingerprint
    assert item.fingerprint == github_monitor._fingerprint(task, 77)


@pytest.mark.django_db
def test_sync_monitor_items_does_not_requeue_dismissed_issue(monkeypatch):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    issue = _issue(
        88, github_monitor.INSTALL_HEALTH_TITLE, github_monitor.INSTALL_HEALTH_MARKER
    )
    monkeypatch.setattr(
        github_monitor.github_service,
        "fetch_repository_issues",
        lambda **_: [issue],
    )

    github_monitor.sync_monitor_items(token="token", now=timezone.now())
    item = GitHubMonitorItem.objects.get(task=task, issue_number=88)
    item.mark_status(GitHubMonitorItem.Status.DISMISSED)

    github_monitor.sync_monitor_items(token="token", now=timezone.now())

    item.refresh_from_db()
    assert item.status == GitHubMonitorItem.Status.DISMISSED
    assert item.completed_at is not None


@pytest.mark.django_db
def test_monitor_cycle_times_out_inactive_terminal_then_launches_next(
    tmp_path, monkeypatch
):
    _enable_monitor_feature()
    _configure_defaults()
    now = timezone.now()
    task = GitHubMonitorTask.objects.get(name="install-health")
    task.inactivity_timeout_minutes = 1
    task.save(update_fields=["inactivity_timeout_minutes"])
    active_pid = tmp_path / "active.pid"
    active_pid.write_text("123\nfake command\n", encoding="utf-8")
    active = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"active").hexdigest(),
        issue_number=10,
        issue_title="Active",
        issue_url="https://github.example/issues/10",
        status=GitHubMonitorItem.Status.ACTIVE,
        terminal_pid_file=str(active_pid),
        launched_at=now - timedelta(minutes=10),
        last_activity_at=now - timedelta(minutes=10),
    )
    queued = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"queued").hexdigest(),
        issue_number=11,
        issue_title="Queued",
        issue_url="https://github.example/issues/11",
        status=GitHubMonitorItem.Status.QUEUED,
    )
    monkeypatch.setattr(github_monitor, "desktop_ui_enabled", lambda: True)
    monkeypatch.setattr(github_monitor, "sync_monitor_items", lambda: {"matched": 0})
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda pid_file: True)
    terminated: list[Path] = []
    monkeypatch.setattr(
        github_monitor,
        "_terminate_terminal",
        lambda pid_file: terminated.append(pid_file) or True,
    )

    def fake_launch(command, *, title: str, state_key: str) -> Path:
        pid_file = tmp_path / f"{state_key}.pid"
        pid_file.write_text("456\nfake command\n", encoding="utf-8")
        return pid_file

    monkeypatch.setattr(github_monitor, "_launch_command", fake_launch)

    result = github_monitor.run_monitor_cycle()

    active.refresh_from_db()
    queued.refresh_from_db()
    assert result["active"]["reason"] == "inactive"
    assert terminated == [active_pid]
    assert active.status == GitHubMonitorItem.Status.TIMED_OUT
    assert queued.status == GitHubMonitorItem.Status.ACTIVE


@pytest.mark.django_db
def test_monitor_cycle_keeps_active_item_when_terminal_termination_fails(
    tmp_path, monkeypatch
):
    _enable_monitor_feature()
    _configure_defaults()
    now = timezone.now()
    task = GitHubMonitorTask.objects.get(name="install-health")
    task.inactivity_timeout_minutes = 1
    task.save(update_fields=["inactivity_timeout_minutes"])
    active_pid = tmp_path / "active.pid"
    active_pid.write_text("123\nfake command\n", encoding="utf-8")
    active = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"active").hexdigest(),
        issue_number=10,
        issue_title="Active",
        issue_url="https://github.example/issues/10",
        status=GitHubMonitorItem.Status.ACTIVE,
        terminal_pid_file=str(active_pid),
        launched_at=now - timedelta(minutes=10),
        last_activity_at=now - timedelta(minutes=10),
    )
    monkeypatch.setattr(github_monitor, "desktop_ui_enabled", lambda: True)
    monkeypatch.setattr(github_monitor, "sync_monitor_items", lambda: {"matched": 0})
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda pid_file: True)
    monkeypatch.setattr(github_monitor, "_terminate_terminal", lambda pid_file: False)

    result = github_monitor.run_monitor_cycle()

    active.refresh_from_db()
    assert result["active"]["reason"] == "terminate_failed"
    assert result["launch"]["reason"] == "launch_disabled"
    assert active.status == GitHubMonitorItem.Status.ACTIVE


@pytest.mark.django_db
def test_monitor_cycle_skips_when_device_lock_is_already_held(tmp_path, monkeypatch):
    _enable_monitor_feature()
    _configure_defaults()
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    lock_path = github_monitor.monitor_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_path), timeout=0):
        result = github_monitor.run_monitor_cycle()

    assert result == {"skipped": True, "reason": "monitor_locked"}


@pytest.mark.django_db
def test_heartbeat_and_complete_update_monitor_item(tmp_path, monkeypatch):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    pid_file = tmp_path / "active.pid"
    pid_file.write_text("123\nfake command\n", encoding="utf-8")
    item = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"item").hexdigest(),
        issue_number=7,
        issue_title="Install health check is failing",
        issue_url="https://github.example/issues/7",
        status=GitHubMonitorItem.Status.ACTIVE,
        terminal_pid_file=str(pid_file),
    )
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda _: True)
    monkeypatch.setattr(github_monitor, "_terminate_terminal", lambda _: True)

    github_monitor.record_activity(item_id=item.pk)
    item.refresh_from_db()
    assert item.last_activity_at is not None

    completed = github_monitor.complete_item(item_id=item.pk)
    completed.refresh_from_db()
    assert completed.status == GitHubMonitorItem.Status.COMPLETED


@pytest.mark.django_db
def test_dismiss_item_terminates_active_terminal(tmp_path, monkeypatch):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    pid_file = tmp_path / "active.pid"
    pid_file.write_text("123\nfake command\n", encoding="utf-8")
    item = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"dismiss").hexdigest(),
        issue_number=8,
        issue_title="Install health check is failing",
        issue_url="https://github.example/issues/8",
        status=GitHubMonitorItem.Status.ACTIVE,
        terminal_pid_file=str(pid_file),
    )
    terminated: list[Path] = []
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda _: True)
    monkeypatch.setattr(
        github_monitor,
        "_terminate_terminal",
        lambda path: terminated.append(path) or True,
    )

    dismissed = github_monitor.dismiss_item(item_id=item.pk)

    dismissed.refresh_from_db()
    assert dismissed.status == GitHubMonitorItem.Status.DISMISSED
    assert terminated == [pid_file]


@pytest.mark.django_db
def test_complete_item_keeps_active_status_when_terminal_termination_fails(
    tmp_path, monkeypatch
):
    _configure_defaults()
    task = GitHubMonitorTask.objects.get(name="install-health")
    pid_file = tmp_path / "active.pid"
    pid_file.write_text("123\nfake command\n", encoding="utf-8")
    item = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint=hashlib.sha256(b"complete-fail").hexdigest(),
        issue_number=9,
        issue_title="Install health check is failing",
        issue_url="https://github.example/issues/9",
        status=GitHubMonitorItem.Status.ACTIVE,
        terminal_pid_file=str(pid_file),
    )
    monkeypatch.setattr(github_monitor, "_terminal_running", lambda _: True)
    monkeypatch.setattr(github_monitor, "_terminate_terminal", lambda _: False)

    with pytest.raises(RuntimeError):
        github_monitor.complete_item(item_id=item.pk)

    item.refresh_from_db()
    assert item.status == GitHubMonitorItem.Status.ACTIVE


@pytest.mark.django_db
def test_monitor_task_failure_emails_admins(monkeypatch):
    captured: list[tuple[str, str]] = []

    def fail_cycle(*, launch: bool = True):
        raise RuntimeError("boom")

    monkeypatch.setattr(github_monitor, "run_monitor_cycle", fail_cycle)
    monkeypatch.setattr(
        github_monitor,
        "notify_admins_of_failure",
        lambda subject, body: captured.append((subject, body)) or True,
    )

    from apps.repos.tasks import monitor_github_readiness

    with pytest.raises(RuntimeError):
        monitor_github_readiness.run()

    assert captured
    assert "GitHub monitor failed" in captured[0][0]
    assert "boom" in captured[0][1]


@pytest.mark.django_db
def test_gh_monitor_command_configure_and_evaluate(monkeypatch):
    monkeypatch.setattr(
        github_monitor.github_service, "get_github_issue_token", lambda: "token"
    )
    monkeypatch.setattr(github_monitor, "desktop_ui_enabled", lambda: True)
    out = StringIO()
    call_command(
        "gh_monitor",
        "--json",
        "configure",
        "--repo",
        "octo/demo",
        "--write",
        stdout=out,
    )
    assert json.loads(out.getvalue())["write"] is True

    out = StringIO()
    call_command("gh_monitor", "--json", "evaluate", stdout=out)
    result = json.loads(out.getvalue())
    assert result["ready"] is True
    assert result["configured_tasks"] == 2


@pytest.mark.django_db
def test_github_monitoring_feature_fixture_loads():
    fixture_path = (
        Path(settings.BASE_DIR)
        / "apps"
        / "features"
        / "fixtures"
        / "features__github_monitoring.json"
    )

    call_command("register_site_apps")
    call_command("loaddata", str(fixture_path))

    feature = Feature.objects.select_related("main_app").get(
        slug=github_monitor.GITHUB_MONITOR_FEATURE_SLUG
    )
    assert feature.main_app is not None
    assert feature.main_app.name == "repos"


def test_github_monitor_is_in_static_beat_schedule(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["github_monitor"]
    assert entry["task"] == "apps.repos.tasks.monitor_github_readiness"
