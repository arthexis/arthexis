from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.repos.admin import (
    GitHubRepositoryAdmin,
    RepositoryIssueAdmin,
    RepositoryPullRequestAdmin,
)
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository
from apps.sites.admin.story_admin import UserStoryAdmin
from apps.sites.models import UserStory


def _make_request():
    return RequestFactory().post("/admin/repos/")


def _staff_user_with_repo_work_view_permissions(username="repo-work-viewer"):
    user = get_user_model().objects.create_user(username=username, is_staff=True)
    permissions = Permission.objects.filter(
        content_type__app_label="repos",
        codename__in=[
            "view_githubrepository",
            "view_repositoryissue",
            "view_repositorypullrequest",
        ],
    )
    user.user_permissions.add(*permissions)
    return user


@pytest.mark.django_db
def test_run_fetch_from_github_action_emits_success_message_and_redirects():
    model_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    request = _make_request()
    model_admin.message_user = Mock()

    response = model_admin._run_fetch_from_github_action(
        request,
        sync_function=lambda: (3, 2),
        error_message_template=_("Failed: %(error)s"),
        success_message_template=_("Fetched %(created)s/%(updated)s"),
        empty_state_message_template=_("No data"),
    )

    assert response.status_code == 302
    model_admin.message_user.assert_called_once_with(
        request,
        "Fetched 3/2",
        level=messages.SUCCESS,
    )


@pytest.mark.django_db


@pytest.mark.django_db
def test_run_fetch_from_github_action_emits_error_message_and_redirects():
    model_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    request = _make_request()
    model_admin.message_user = Mock()

    def failing_sync():
        raise RuntimeError("boom")

    response = model_admin._run_fetch_from_github_action(
        request,
        sync_function=failing_sync,
        error_message_template=_("Failed: %(error)s"),
        success_message_template=_("Fetched %(created)s/%(updated)s"),
        empty_state_message_template=_("No data"),
    )

    assert response.status_code == 302
    model_admin.message_user.assert_called_once_with(
        request,
        "Failed: boom",
        level=messages.ERROR,
    )


@pytest.mark.django_db
def test_fetch_open_actions_delegate_to_shared_helper(monkeypatch):
    issue_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    pull_request_admin = RepositoryPullRequestAdmin(RepositoryPullRequest, admin.site)
    request = _make_request()
    sentinel = object()
    issue_call = {}
    pull_request_call = {}

    def fake_issue_runner(req, **kwargs):
        issue_call["request"] = req
        issue_call.update(kwargs)
        return sentinel

    def fake_pull_request_runner(req, **kwargs):
        pull_request_call["request"] = req
        pull_request_call.update(kwargs)
        return sentinel

    monkeypatch.setattr(issue_admin, "_run_fetch_from_github_action", fake_issue_runner)
    monkeypatch.setattr(
        pull_request_admin,
        "_run_fetch_from_github_action",
        fake_pull_request_runner,
    )

    issue_result = issue_admin.fetch_open_issues(request)
    pull_request_result = pull_request_admin.fetch_open_pull_requests(request)
    issue_sync_function = issue_call["sync_function"]
    pull_request_sync_function = pull_request_call["sync_function"]

    assert issue_result is sentinel
    assert issue_call["request"] is request
    assert issue_sync_function.__self__ is RepositoryIssue
    assert issue_sync_function.__func__ is RepositoryIssue.fetch_open_issues.__func__
    assert str(issue_call["error_message_template"]) == "Failed to fetch issues from GitHub: %(error)s"
    assert (
        str(issue_call["success_message_template"])
        == "Fetched %(created)s new and %(updated)s updated issues."
    )
    assert str(issue_call["empty_state_message_template"]) == "No open issues found to sync."

    assert pull_request_result is sentinel
    assert pull_request_call["request"] is request
    assert pull_request_sync_function.__self__ is RepositoryPullRequest
    assert (
        pull_request_sync_function.__func__
        is RepositoryPullRequest.fetch_open_pull_requests.__func__
    )
    assert (
        str(pull_request_call["error_message_template"])
        == "Failed to fetch pull requests from GitHub: %(error)s"
    )
    assert (
        str(pull_request_call["success_message_template"])
        == "Fetched %(created)s new and %(updated)s updated pull requests."
    )
    assert (
        str(pull_request_call["empty_state_message_template"])
        == "No open pull requests found to sync."
    )


@pytest.mark.django_db
def test_repository_work_dashboard_actions_point_to_shared_view():
    request = RequestFactory().get("/admin/")
    request.user = get_user_model().objects.create_superuser(username="super")
    issue_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    pull_request_admin = RepositoryPullRequestAdmin(RepositoryPullRequest, admin.site)
    user_story_admin = UserStoryAdmin(UserStory, admin.site)
    expected_url = reverse("repos:repository-work-dashboard")

    assert issue_admin.get_dashboard_actions(request) == ["repository_work_dashboard"]
    assert pull_request_admin.get_dashboard_actions(request) == [
        "repository_work_dashboard"
    ]
    assert user_story_admin.get_dashboard_actions(request) == (
        "repository_work_dashboard",
    )

    assert issue_admin.repository_work_dashboard(request).url == expected_url
    assert pull_request_admin.repository_work_dashboard(request).url == expected_url
    assert user_story_admin.repository_work_dashboard(request).url == expected_url


@pytest.mark.django_db
def test_github_repository_work_action_does_not_require_token_setup_permission():
    request = RequestFactory().get("/admin/")
    request.user = _staff_user_with_repo_work_view_permissions()
    repository_admin = GitHubRepositoryAdmin(GitHubRepository, admin.site)

    assert repository_admin.get_dashboard_actions(request) == [
        "repository_work_dashboard"
    ]


@pytest.mark.django_db
def test_repository_work_dashboard_actions_require_shared_view_permissions():
    request = RequestFactory().get("/admin/")
    request.user = get_user_model().objects.create_user(
        username="issue-only-viewer",
        is_staff=True,
    )
    issue_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    pull_request_admin = RepositoryPullRequestAdmin(RepositoryPullRequest, admin.site)
    user_story_admin = UserStoryAdmin(UserStory, admin.site)

    assert issue_admin.get_dashboard_actions(request) == []
    assert pull_request_admin.get_dashboard_actions(request) == []
    assert user_story_admin.get_dashboard_actions(request) == ()
