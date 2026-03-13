from __future__ import annotations

from datetime import timedelta
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.maps.models import Location
from apps.tasks.models import GitHubIssueTemplate, ManualTaskReport, ManualTaskRequest
from apps.tasks.tasks import create_manual_task_github_issue

pytestmark = pytest.mark.django_db


def build_manual_task_request(**overrides) -> ManualTaskRequest:
    """Create and return a minimal manual task request instance."""

    location = overrides.pop("location", None) or Location.objects.create(name="HQ")
    now = timezone.now()
    defaults = {
        "description": "Replace damaged connector",
        "location": location,
        "scheduled_start": now + timedelta(hours=1),
        "scheduled_end": now + timedelta(hours=2),
    }
    defaults.update(overrides)
    return ManualTaskRequest.objects.create(**defaults)


def test_manual_task_request_requires_overdue_threshold_for_overdue_trigger() -> None:
    """Overdue-triggered issue automation requires a positive threshold."""

    template = GitHubIssueTemplate.objects.create(
        name="Operations overdue",
        title_template="Overdue maintenance task",
        body_template="Task details",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="overdue",
    )

    with pytest.raises(ValidationError) as excinfo:
        task.full_clean()

    assert "github_issue_overdue_after" in excinfo.value.message_dict


def test_schedule_github_issue_overdue_uses_eta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overdue trigger schedules the Celery task for the computed future ETA."""

    calls: list[dict] = []

    def fake_schedule(task, *, args=None, kwargs=None, require_enabled=True, **options):
        calls.append(
            {
                "task": task,
                "args": args,
                "kwargs": kwargs,
                "require_enabled": require_enabled,
                "options": options,
            }
        )
        return True

    monkeypatch.setattr("apps.celery.utils.schedule_task", fake_schedule)

    template = GitHubIssueTemplate.objects.create(
        name="Overdue task",
        title_template="Task overdue",
        body_template="Task body",
    )
    now = timezone.now()
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="overdue",
        github_issue_overdue_after=timedelta(hours=6),
        scheduled_start=now + timedelta(hours=1),
        scheduled_end=now + timedelta(hours=2),
    )

    task.schedule_github_issue()

    assert len(calls) == 1
    scheduled_eta = calls[0]["options"]["eta"]
    assert scheduled_eta == task.scheduled_start + timedelta(hours=6)


def test_create_manual_task_github_issue_creates_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Celery task creates and stores a GitHub issue when the trigger is eligible."""

    template = GitHubIssueTemplate.objects.create(
        name="Task starts",
        title_template="Start maintenance",
        body_template="Handle this now.",
        labels="ops,maintenance",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="scheduled_start",
        scheduled_start=timezone.now() - timedelta(minutes=5),
        scheduled_end=timezone.now() + timedelta(minutes=55),
    )

    class FakeResponse:
        def json(self):
            return {
                "html_url": "https://github.com/acme/repo/issues/101",
                "number": 101,
            }

    class FakeIssueClient:
        def create(self, title, body, labels=None):
            assert title == "Start maintenance"
            assert body == "Handle this now."
            assert labels == ["ops", "maintenance"]
            return FakeResponse()

    monkeypatch.setattr(
        "apps.repos.services.github.GitHubIssue.from_active_repository",
        lambda: FakeIssueClient(),
    )

    issue_url = create_manual_task_github_issue(task.pk, "scheduled_start")
    task.refresh_from_db()

    assert issue_url == "https://github.com/acme/repo/issues/101"
    assert task.github_issue_url == issue_url
    assert task.github_issue_number == 101
    assert task.github_issue_opened_at is not None


def test_report_creation_enqueues_completed_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating a task report queues completed-trigger issue automation."""

    template = GitHubIssueTemplate.objects.create(
        name="Completion",
        title_template="Completed manual task",
        body_template="Task is complete.",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="completed",
    )

    calls: list[tuple] = []

    def fake_enqueue(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return True

    monkeypatch.setattr("apps.celery.utils.enqueue_task", fake_enqueue)

    ManualTaskReport.objects.create(request=task, details="Done")

    assert len(calls) == 1
    assert calls[0][1] == (task.pk, "completed")


def test_scheduled_start_trigger_rejects_stale_early_job() -> None:
    """Scheduled-start trigger only opens at or after the current scheduled start."""

    template = GitHubIssueTemplate.objects.create(
        name="Task starts",
        title_template="Start maintenance",
        body_template="Handle this now.",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="scheduled_start",
        scheduled_start=timezone.now() + timedelta(hours=2),
        scheduled_end=timezone.now() + timedelta(hours=3),
    )

    assert not task.can_open_github_issue_for_trigger("scheduled_start")


def test_overdue_trigger_rejects_stale_early_job() -> None:
    """Overdue trigger only opens at or after the computed overdue threshold."""

    template = GitHubIssueTemplate.objects.create(
        name="Task overdue",
        title_template="Overdue maintenance",
        body_template="Handle this now.",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="overdue",
        github_issue_overdue_after=timedelta(hours=4),
        scheduled_start=timezone.now() - timedelta(hours=1),
        scheduled_end=timezone.now() + timedelta(hours=1),
    )

    assert not task.can_open_github_issue_for_trigger("overdue")


@pytest.mark.pr_origin(6182)
def test_schedule_github_issue_uses_cross_process_cache_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling should dedupe duplicate trigger/ETA combinations via shared cache."""

    calls: list[dict] = []

    def fake_schedule(task, *, args=None, kwargs=None, require_enabled=True, **options):
        calls.append({
            "task": task,
            "args": args,
            "kwargs": kwargs,
            "require_enabled": require_enabled,
            "options": options,
        })
        return True

    monkeypatch.setattr("apps.celery.utils.schedule_task", fake_schedule)

    template = GitHubIssueTemplate.objects.create(
        name="Start task",
        title_template="Task starts",
        body_template="Start now.",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="scheduled_start",
    )

    eta = task.scheduled_start
    calls.clear()
    cache.delete(task._github_issue_schedule_cache_key("scheduled_start", eta))

    task.schedule_github_issue()
    task.schedule_github_issue()

    assert len(calls) == 1


@pytest.mark.pr_origin(6182)
def test_create_manual_task_github_issue_skips_early_scheduled_start() -> None:
    """Task should not create issue before scheduled start time."""

    template = GitHubIssueTemplate.objects.create(
        name="Task starts",
        title_template="Start maintenance",
        body_template="Handle this now.",
    )
    task = build_manual_task_request(
        github_issue_template=template,
        github_issue_trigger="scheduled_start",
        scheduled_start=timezone.now() + timedelta(hours=2),
        scheduled_end=timezone.now() + timedelta(hours=3),
    )

    issue_url = create_manual_task_github_issue(task.pk, "scheduled_start")

    assert issue_url is None
