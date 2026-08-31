from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import RequestFactory, override_settings

import apps.sites.admin.story_admin as story_admin
import apps.sites.models.user_story as user_story_models
from apps.core.services.operator_interrupts import (
    drain_operator_interrupts,
    operator_local_feedback_lock_path,
)
from apps.sites.admin.story_admin import UserStoryAdmin
from apps.sites.models import UserStory, parse_feedback_tags
from apps.sites.tasks import create_user_story_github_issue

pytestmark = pytest.mark.django_db

RATING_LABEL_CASES = (
    (1, ["feedback", "bug", "priority: high"]),
    (2, ["feedback", "bug"]),
    (3, ["feedback", "bug", "priority: low"]),
    (4, ["feedback", "enhancement"]),
    (5, ["feedback", "enhancement", "priority: low"]),
)


def _source_line(body: str) -> str:
    return next(line for line in body.splitlines() if line.startswith("**Source:**"))


@pytest.mark.parametrize(("rating", "expected_labels"), RATING_LABEL_CASES)
def test_user_story_github_issue_labels_include_rating_labels(
    rating: int, expected_labels: list[str]
) -> None:
    story = UserStory(rating=rating, path="/feedback/", comments="Feedback")

    assert story.get_github_issue_labels() == expected_labels


def test_privileged_feedback_hashtags_apply_existing_labels_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.repos.github.fetch_issue_label_names",
        lambda: ["bug", "enhancement", "RPi-Debian"],
    )
    story = UserStory.objects.create(
        rating=4,
        path="/feedback/",
        comments="Needs #Bug #bug #rpi-debian #Enhancement #unknown.",
        allow_feedback_issue_label_tags=True,
    )

    assert story.get_github_issue_labels() == [
        "feedback",
        "enhancement",
        "bug",
        "RPi-Debian",
    ]


def test_privileged_feedback_unknown_hashtags_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.repos.github.fetch_issue_label_names", lambda: ["bug"])
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Useful report with #unknown.",
        allow_feedback_issue_label_tags=True,
    )

    assert story.get_github_issue_labels() == [
        "feedback",
        "enhancement",
        "priority: low",
    ]


def test_privileged_feedback_label_lookup_failure_keeps_default_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.repos.services.github import GitHubRepositoryError

    def raise_label_error() -> list[str]:
        raise GitHubRepositoryError("GitHub token is not configured")

    monkeypatch.setattr("apps.repos.github.fetch_issue_label_names", raise_label_error)
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Useful report with #bug.",
        allow_feedback_issue_label_tags=True,
    )

    assert story.get_github_issue_labels() == [
        "feedback",
        "enhancement",
        "priority: low",
    ]


def test_non_privileged_feedback_hashtags_do_not_apply_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.repos.github.fetch_issue_label_names",
        lambda: pytest.fail("Non-privileged feedback must not fetch repository labels"),
    )
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Useful report with #bug.",
    )

    assert story.get_github_issue_labels() == [
        "feedback",
        "enhancement",
        "priority: low",
    ]


@pytest.mark.parametrize(("rating", "expected_labels"), RATING_LABEL_CASES)
def test_create_github_issue_passes_rating_labels_to_github(
    monkeypatch: pytest.MonkeyPatch, rating: int, expected_labels: list[str]
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {
                "html_url": f"https://github.example/issues/{rating}",
                "number": rating,
            }

        def close(self) -> None:
            return None

    def fake_create_issue(
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
        fingerprint: str | None = None,
    ) -> FakeResponse:
        calls.append(
            {
                "title": title,
                "body": body,
                "labels": labels,
                "fingerprint": fingerprint,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("apps.repos.github.create_issue", fake_create_issue)
    story = UserStory.objects.create(
        rating=rating,
        path="/feedback/",
        comments="Feedback from the admin queue.",
    )

    issue_url = story.create_github_issue()

    assert issue_url == f"https://github.example/issues/{rating}"
    assert calls[0]["labels"] == expected_labels


def test_create_github_issue_passes_privileged_hashtag_labels_to_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {
                "html_url": "https://github.example/issues/42",
                "number": 42,
            }

        def close(self) -> None:
            return None

    def fake_create_issue(
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
        fingerprint: str | None = None,
    ) -> FakeResponse:
        del title, body, fingerprint
        calls.append({"labels": labels})
        return FakeResponse()

    monkeypatch.setattr("apps.repos.github.fetch_issue_label_names", lambda: ["bug"])
    monkeypatch.setattr("apps.repos.github.create_issue", fake_create_issue)
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Useful privileged report with #Bug.",
        allow_feedback_issue_label_tags=True,
    )

    issue_url = story.create_github_issue()

    assert issue_url == "https://github.example/issues/42"
    assert calls == [{"labels": ["feedback", "enhancement", "priority: low", "bug"]}]


def test_admin_manual_issue_action_uses_shared_rating_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Five-star admin-created issue.",
    )
    model_admin = UserStoryAdmin(UserStory, admin.site)

    def fake_create_github_issue(self: UserStory) -> str:
        calls.append(self.get_github_issue_labels())
        return "https://github.example/issues/5"

    monkeypatch.setattr(UserStory, "create_github_issue", fake_create_github_issue)
    monkeypatch.setattr(model_admin, "message_user", lambda *args, **kwargs: None)

    model_admin.create_github_issues(object(), UserStory.objects.filter(pk=story.pk))

    assert calls == [["feedback", "enhancement", "priority: low"]]


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_authenticated_user_story_ratings_enqueue_when_other_conditions_hold(
    monkeypatch: pytest.MonkeyPatch, rating: int
) -> None:
    monkeypatch.setattr("apps.sites.models.user_story.is_celery_enabled", lambda: True)
    user = get_user_model().objects.create_user(username=f"feedback-{rating}")
    story = UserStory(
        rating=rating,
        path="/feedback/",
        comments="Authenticated feedback",
        user=user,
    )

    assert story.should_enqueue_github_issue(created=True, raw=False)


def test_user_story_github_issue_enqueue_keeps_existing_non_rating_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.sites.models.user_story.is_celery_enabled", lambda: True)
    user = get_user_model().objects.create_user(username="feedback-gates")
    story = UserStory(
        rating=5,
        path="/feedback/",
        comments="Authenticated feedback",
        user=user,
    )

    assert not story.should_enqueue_github_issue(created=False, raw=False)
    assert not story.should_enqueue_github_issue(created=True, raw=True)
    assert not UserStory(
        rating=5,
        path="/feedback/",
        comments="Already linked",
        user=user,
        github_issue_url="https://github.example/issues/1",
    ).should_enqueue_github_issue(created=True, raw=False)
    assert not UserStory(
        rating=5,
        path="/feedback/",
        comments="Anonymous feedback",
    ).should_enqueue_github_issue(created=True, raw=False)

    monkeypatch.setattr("apps.sites.models.user_story.is_celery_enabled", lambda: False)

    assert not story.should_enqueue_github_issue(created=True, raw=False)


def test_user_story_github_issue_enqueue_requires_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.sites.models.user_story.is_celery_enabled", lambda: True)
    monkeypatch.setattr(user_story_models, "REPOS_APP_INSTALLED", False)
    user = get_user_model().objects.create_user(username="feedback-no-repos")
    story = UserStory(
        rating=5,
        path="/feedback/",
        comments="Authenticated feedback",
        user=user,
    )

    assert not story.should_enqueue_github_issue(created=True, raw=False)


def test_create_user_story_github_issue_skips_without_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_story_models, "REPOS_APP_INSTALLED", False)
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Five-star feedback should stay local when Repos is absent.",
    )

    assert story.create_github_issue() is None
    story.refresh_from_db()
    assert story.github_issue_url == ""
    assert story.github_issue_number is None


def test_user_story_admin_hides_github_issue_action_without_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(story_admin, "REPOS_APP_INSTALLED", False)
    request = RequestFactory().get("/admin/pages/userstory/")
    request.user = get_user_model().objects.create_user(
        username="story-admin",
        is_staff=True,
        is_superuser=True,
    )
    model_admin = UserStoryAdmin(UserStory, admin.site)

    assert "create_github_issues" not in model_admin.get_actions(request)
    assert model_admin.get_dashboard_actions(request) == ()


def test_create_user_story_github_issue_handles_rating_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_url = "https://github.example/issues/5"
    calls: list[int] = []
    story = UserStory.objects.create(
        rating=5,
        path="/feedback/",
        comments="Five-star feedback should still create an issue.",
    )

    def fake_create_github_issue(self: UserStory) -> str:
        calls.append(self.pk)
        return issue_url

    monkeypatch.setattr(UserStory, "create_github_issue", fake_create_github_issue)

    assert create_user_story_github_issue(story.pk) == issue_url
    assert calls == [story.pk]


def test_feedback_issue_title_includes_node_role_and_sanitized_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.repos.github_monitor.local_node_role", lambda: "Terminal")
    story = UserStory(
        rating=2,
        path="https://viewer:secret@feedback.example.com:8443/feedback/?token=abc",
        comments="Title should identify the node role without leaking URL details.",
    )

    title = story.build_github_issue_title()

    assert title == "[Terminal] Feedback for /feedback/ (2/5)"
    assert "secret" not in title
    assert "token=abc" not in title


def test_feedback_issue_title_strips_query_but_body_keeps_submitted_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.repos.github_monitor.local_node_role", lambda: "Watchtower"
    )
    submitted_path = (
        "/admin/pages/userstory/?issue_destination__exact=local&status__exact=open"
        "#results"
    )
    story = UserStory(
        rating=4,
        path=submitted_path,
        comments="Title should stay short while body keeps the submitted path.",
    )

    assert story.build_github_issue_title() == (
        "[Watchtower] Feedback for /admin/pages/userstory/ (4/5)"
    )
    assert f"**Path:** {submitted_path}" in story.build_github_issue_body()


@override_settings(TIME_ZONE="UTC")
def test_feedback_issue_body_includes_node_role_and_minute_submitted_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.repos.github_monitor.local_node_role", lambda: "Control")
    story = UserStory(
        rating=4,
        path="/feedback/",
        comments="Body should include role and a less granular timestamp.",
        submitted_at=datetime(2026, 5, 31, 14, 25, 36, 987654, tzinfo=timezone.utc),
    )

    body = story.build_github_issue_body()

    assert "**Node role:** Control" in body
    assert "**Submitted at:** 2026-05-31 14:25 UTC" in body
    assert "14:25:36" not in body
    assert "987654" not in body


def test_feedback_issue_body_omits_form_fields_and_adds_source_domain() -> None:
    story = UserStory(
        rating=4,
        path="/feedback/",
        comments="Useful report from users.",
        name="Taylor",
        contact_via_chat=False,
        messages="In Dashboard (`https://portal.example.com/admin/`).",
    )

    body = story.build_github_issue_body()

    assert "**Path:** /feedback/" in body
    assert "**Source:** portal.example.com" in body
    assert "**Rating:**" not in body
    assert "**Name:**" not in body
    assert "**Contact via chat:**" not in body


def test_feedback_issue_body_uses_referer_domain_when_path_is_relative() -> None:
    story = UserStory(
        rating=4,
        path="/feedback/?next=%2Fdashboard%2F",
        referer="https://portal.example.com/admin/dashboard/",
        comments="Useful report from users.",
        messages="No absolute URL in this text.",
    )

    body = story.build_github_issue_body()

    assert "**Source:** portal.example.com" in body


def test_feedback_issue_source_uses_hostname_without_credentials_or_port() -> None:
    story = UserStory(
        rating=4,
        path="https://viewer:secret@feedback.example.com:8443/feedback/",
        referer="https://agent:token@backup.example.net:9443/ref",
        comments="Source label should stay domain-only.",
        messages=(
            "See https://alice:hunter2@portal.example.com:4443/help for details."
        ),
    )

    body = story.build_github_issue_body()

    source = _source_line(body)
    assert source == "**Source:** backup.example.net"
    assert "agent" not in source
    assert "token" not in source
    assert ":9443" not in source


def test_feedback_issue_source_message_fallback_uses_hostname_only() -> None:
    story = UserStory(
        rating=4,
        path="/feedback/",
        comments="Source label should stay domain-only.",
        messages=(
            "See https://alice:hunter2@portal.example.com:4443/help for details."
        ),
    )

    body = story.build_github_issue_body()

    source = _source_line(body)
    assert source == "**Source:** portal.example.com"
    assert "alice" not in source
    assert "hunter2" not in source
    assert ":4443" not in source


def test_feedback_issue_source_prefers_referer_over_message_urls() -> None:
    story = UserStory(
        rating=4,
        path="/feedback/",
        referer="https://portal.example.com/admin/",
        comments="Triage source should follow request origin first.",
        messages="Documentation link: https://docs.example.org/guides/faq/",
    )

    body = story.build_github_issue_body()

    assert "**Source:** portal.example.com" in body


def test_feedback_issue_body_sanitizes_absolute_path_value() -> None:
    story = UserStory(
        rating=4,
        path=(
            "https://viewer:secret@feedback.example.com:8443/feedback/"
            "?token=abc#fragment"
        ),
        comments="Absolute path should be sanitized in issue body.",
    )

    body = story.build_github_issue_body()

    assert "**Path:** /feedback/" in body
    assert "secret" not in body
    assert "token=abc" not in body


def test_parse_feedback_tags_normalizes_and_deduplicates_tags() -> None:
    assert parse_feedback_tags(
        "Please keep this #Local and review #Needs-Triage.",
        "Repeated #local #LOCAL and separate #admin_note.",
    ) == ["local", "needs-triage", "admin_note"]


def test_parse_feedback_tags_ignores_url_fragments() -> None:
    assert parse_feedback_tags(
        "See https://docs.example/#local before routing this feedback.",
        "A normal routing tag still works after the link: #Local.",
    ) == ["local"]


def test_url_fragment_named_local_does_not_route_story_to_local_queue() -> None:
    story = UserStory.objects.create(
        rating=2,
        path="/feedback/",
        comments="See https://docs.example/#local before filing this feedback.",
    )

    assert story.feedback_tags == []
    assert story.issue_destination == UserStory.IssueDestination.GITHUB
    assert not story.is_local_issue


def test_local_feedback_tag_routes_story_to_local_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.sites.models.user_story.is_celery_enabled", lambda: True)
    user = get_user_model().objects.create_user(username="local-feedback")

    story = UserStory.objects.create(
        rating=2,
        path="/feedback/",
        comments="Please keep this report #local for the system admin.",
        user=user,
    )

    assert story.feedback_tags == ["local"]
    assert story.issue_destination == UserStory.IssueDestination.LOCAL
    assert story.is_local_issue
    assert not story.should_enqueue_github_issue(created=True, raw=False)


def test_superuser_local_feedback_appends_operator_interrupt(
    tmp_path,
    django_capture_on_commit_callbacks,
) -> None:
    user = get_user_model().objects.create_superuser(username="operator")

    with override_settings(BASE_DIR=tmp_path):
        with django_capture_on_commit_callbacks(execute=True):
            story = UserStory.objects.create(
                rating=2,
                path="/feedback/",
                comments="Please keep this report #local for the system admin.",
                user=user,
            )
        drained = drain_operator_interrupts(base_dir=tmp_path)

    assert drained["entries"][0]["source"] == "user_story_local_feedback"
    assert drained["entries"][0]["user_story_id"] == story.pk
    assert drained["entries"][0]["username"] == "operator"
    assert drained["entries"][0]["is_superuser"] is True


def test_non_superuser_local_feedback_does_not_append_operator_interrupt(
    tmp_path,
) -> None:
    user = get_user_model().objects.create_user(username="local-feedback")

    with override_settings(BASE_DIR=tmp_path):
        UserStory.objects.create(
            rating=2,
            path="/feedback/",
            comments="Please keep this report #local for the system admin.",
            user=user,
        )

    assert not operator_local_feedback_lock_path(tmp_path).exists()


def test_superuser_update_into_local_feedback_appends_operator_interrupt(
    tmp_path,
    django_capture_on_commit_callbacks,
) -> None:
    user = get_user_model().objects.create_superuser(username="operator-update")

    with override_settings(BASE_DIR=tmp_path):
        story = UserStory.objects.create(
            rating=3,
            path="/feedback/",
            comments="Route this normally.",
            user=user,
        )
        assert not operator_local_feedback_lock_path(tmp_path).exists()

        with django_capture_on_commit_callbacks(execute=True):
            story.comments = "Route this #local after review."
            story.save(update_fields=["comments"])
        drained = drain_operator_interrupts(base_dir=tmp_path)

    assert drained["entries"][0]["user_story_id"] == story.pk
    assert drained["entries"][0]["feedback_tags"] == ["local"]


@pytest.mark.django_db(transaction=True)
def test_superuser_local_feedback_interrupt_waits_for_transaction_commit(
    tmp_path,
) -> None:
    user = get_user_model().objects.create_superuser(username="operator-commit")

    with override_settings(BASE_DIR=tmp_path):
        with transaction.atomic():
            story = UserStory.objects.create(
                rating=2,
                path="/feedback/",
                comments="Please keep this #local report for the system admin.",
                user=user,
            )
            assert not operator_local_feedback_lock_path(tmp_path).exists()

        drained = drain_operator_interrupts(base_dir=tmp_path)

    assert drained["entries"][0]["user_story_id"] == story.pk
    assert drained["entries"][0]["username"] == "operator-commit"


@pytest.mark.django_db(transaction=True)
def test_superuser_local_feedback_interrupt_skips_rolled_back_transaction(
    tmp_path,
) -> None:
    user = get_user_model().objects.create_superuser(username="operator-rollback")

    with override_settings(BASE_DIR=tmp_path):
        with pytest.raises(RuntimeError, match="rollback feedback"):
            with transaction.atomic():
                UserStory.objects.create(
                    rating=2,
                    path="/feedback/",
                    comments="Please keep this #local report for the system admin.",
                    user=user,
                )
                raise RuntimeError("rollback feedback")

    assert not operator_local_feedback_lock_path(tmp_path).exists()


def test_local_feedback_tag_in_messages_routes_story_to_local_queue() -> None:
    story = UserStory.objects.create(
        rating=3,
        path="/feedback/",
        comments="The page needs local review.",
        messages="Context: local operator note #local",
    )

    assert story.feedback_tags == ["local"]
    assert story.issue_destination == UserStory.IssueDestination.LOCAL


def test_manual_issue_routing_update_fields_are_preserved() -> None:
    story = UserStory.objects.create(
        rating=3,
        path="/feedback/",
        comments="This report has no routing tags.",
    )

    story.feedback_tags = ["manual"]
    story.issue_destination = UserStory.IssueDestination.LOCAL
    story.save(update_fields=["feedback_tags", "issue_destination"])
    story.refresh_from_db()

    assert story.feedback_tags == ["manual"]
    assert story.issue_destination == UserStory.IssueDestination.LOCAL


def test_local_feedback_does_not_upload_to_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        "apps.repos.github.create_issue",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    story = UserStory.objects.create(
        rating=2,
        path="/feedback/",
        comments="Keep this one #local.",
    )

    assert story.create_github_issue() is None
    assert calls == []


def test_admin_manual_issue_action_skips_local_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    local_story = UserStory.objects.create(
        rating=2,
        path="/feedback/",
        comments="Keep this one #local.",
    )
    github_story = UserStory.objects.create(
        rating=4,
        path="/feedback/",
        comments="This can become a GitHub issue.",
    )
    model_admin = UserStoryAdmin(UserStory, admin.site)

    def fake_create_github_issue(self: UserStory) -> str:
        calls.append(self.pk)
        return "https://github.example/issues/8"

    monkeypatch.setattr(UserStory, "create_github_issue", fake_create_github_issue)
    monkeypatch.setattr(model_admin, "message_user", lambda *args, **kwargs: None)

    model_admin.create_github_issues(
        object(), UserStory.objects.filter(pk__in=[local_story.pk, github_story.pk])
    )

    assert calls == [github_story.pk]
