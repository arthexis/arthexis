from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.contrib import admin, messages
from django.test import RequestFactory
from django.utils.translation import gettext_lazy as _

from apps.repos.admin import RepositoryIssueAdmin, RepositoryPullRequestAdmin
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest


def _make_request():
    return RequestFactory().post("/admin/repos/")


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
def test_run_fetch_from_github_action_emits_empty_state_message_and_redirects():
    model_admin = RepositoryIssueAdmin(RepositoryIssue, admin.site)
    request = _make_request()
    model_admin.message_user = Mock()

    response = model_admin._run_fetch_from_github_action(
        request,
        sync_function=lambda: (0, 0),
        error_message_template=_("Failed: %(error)s"),
        success_message_template=_("Fetched %(created)s/%(updated)s"),
        empty_state_message_template=_("No data"),
    )

    assert response.status_code == 302
    model_admin.message_user.assert_called_once_with(
        request,
        "No data",
        level=messages.INFO,
    )


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
