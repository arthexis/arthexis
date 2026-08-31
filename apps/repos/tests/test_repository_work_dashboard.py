from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.nodes.models import Node, NodeRole
from apps.repos.models import (
    GitHubMonitorItem,
    GitHubMonitorTask,
    GitHubRepository,
    RepositoryIssue,
    RepositoryPullRequest,
    RepositoryWorkAssignment,
    RepositoryWorkNodeSnapshot,
)
from apps.repos.views import dashboard


def _staff_user():
    return get_user_model().objects.create_user(
        username="repo-work-staff",
        password="password123",
        is_staff=True,
        is_superuser=True,
    )


def _named_staff_user(username):
    return get_user_model().objects.create_user(
        username=username,
        password="password123",
        is_staff=True,
        is_superuser=True,
    )


def _staff_user_with_repo_permissions(*permission_codenames):
    user = get_user_model().objects.create_user(
        username="repo-work-limited-staff",
        password="password123",
        is_staff=True,
    )
    permissions = Permission.objects.filter(
        content_type__app_label="repos",
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


def _repository(owner="arthexis", name="arthexis"):
    return GitHubRepository.objects.create(owner=owner, name=name)


def _issue(repository, number=101, title="Issue work", state="open", labels=None):
    return RepositoryIssue.objects.create(
        repository=repository,
        number=number,
        title=title,
        state=state,
        labels=labels or [],
        html_url=f"https://github.example/{repository.owner}/{repository.name}/issues/{number}",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _pull_request(repository, number=102, title="PR work", state="open", labels=None):
    return RepositoryPullRequest.objects.create(
        repository=repository,
        number=number,
        title=title,
        state=state,
        labels=labels or [],
        html_url=f"https://github.example/{repository.owner}/{repository.name}/pull/{number}",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _role(name="Terminal"):
    return NodeRole.objects.create(name=name, acronym=name[:4].upper())


def _node(hostname="local-node", *, role=None):
    return Node.objects.create(hostname=hostname, public_endpoint=hostname, role=role)


@pytest.mark.django_db
def test_repository_work_dashboard_redirects_anonymous_to_site_login(client):
    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('pages:login')}?")
    assert "next=" in response.url
    assert reverse("admin:login") not in response.url


@pytest.mark.django_db
def test_repository_work_dashboard_shows_issues_prs_and_monitor_marker(client):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository)
    pull_request = _pull_request(repository)
    task = GitHubMonitorTask.objects.create(
        name="approved-issue",
        display="Approved issue",
        repository=repository,
        terminal_state_key="approved-issue",
    )
    monitor_item = GitHubMonitorItem.objects.create(
        task=task,
        fingerprint="issue-101",
        target_type=GitHubMonitorTask.TargetType.ISSUE,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_url=issue.html_url,
        status=GitHubMonitorItem.Status.ACTIVE,
    )

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    assert response.context["selected_repository"] == repository
    content = response.content.decode()
    assert '<meta http-equiv="refresh" content="60">' in content
    assert "Issues (1)" in content
    assert "Pull requests (1)" in content
    assert issue.title in content
    assert pull_request.title in content
    assert "Tracked" in content
    assert "Active" in content
    assert (
        reverse("admin:repos_githubmonitoritem_change", args=[monitor_item.pk])
        in content
    )


@pytest.mark.django_db
def test_repository_work_dashboard_highlights_local_assignments(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    pull_request = _pull_request(repository)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=False,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert issue.title in content
    assert pull_request.title in content
    assert "Assigned here" in content
    assert 'value="assign-local"' in content
    assert 'value="authorize-local-patchwork"' in content
    assert 'value="remove-local-assignment"' in content


@pytest.mark.django_db
def test_repository_work_dashboard_assigns_work_to_local_node(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assignment = RepositoryWorkAssignment.objects.get(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
    )
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment.assigned_by == user


@pytest.mark.django_db
def test_repository_work_dashboard_assigns_work_to_selected_downstream_node(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    local_node = _node("local-node")
    downstream = _node("downstream-node")
    issue = _issue(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: local_node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "assignment_node": downstream.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assignment = RepositoryWorkAssignment.objects.get(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
    )
    assert response.status_code == 302
    assert f"assignment_node={downstream.pk}" in response.url
    assert assignment.node == downstream
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED


@pytest.mark.django_db
def test_repository_work_dashboard_reassignment_refreshes_assigned_at(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    stale_assigned_at = timezone.now() - timedelta(days=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        assigned_at=stale_assigned_at,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "")

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment.assigned_by == user
    assert assignment.assigned_at > stale_assigned_at


@pytest.mark.django_db
def test_repository_work_dashboard_authorizes_assigned_work_for_local_patchwork(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "")

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE
    assert assignment.assigned_by == user


@pytest.mark.django_db
def test_repository_work_dashboard_marks_control_patchwork_as_operator_authorized(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node("gway-001", role=_role("Control"))
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "")

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE
    assert "operator-authorized-control-patchwork" in assignment.reason


@pytest.mark.django_db
def test_repository_work_dashboard_uses_reported_role_for_control_patchwork_marker(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node("gway-001", role=_role("Terminal"))
    RepositoryWorkNodeSnapshot.objects.create(
        node=node,
        capabilities={
            "node_role": "Control",
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    )
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "")

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE
    assert "operator-authorized-control-patchwork" in assignment.reason


@pytest.mark.django_db
def test_repository_work_dashboard_authorize_patchwork_preserves_assigner(
    client,
    monkeypatch,
):
    assigner = _staff_user()
    authorizer = _named_staff_user("repo-work-authorizer")
    client.force_login(authorizer)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=assigner,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE
    assert assignment.assigned_by == assigner


@pytest.mark.django_db
def test_repository_work_dashboard_authorize_local_patchwork_requires_assignment(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "change_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assert response.status_code == 302
    assert RepositoryWorkAssignment.objects.count() == 0
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert messages == [
        f"Assign #{pull_request.number} before authorizing local patchwork."
    ]


@pytest.mark.django_db
def test_repository_work_dashboard_authorize_local_patchwork_rejects_removed_assignment(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "change_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED


@pytest.mark.django_db
def test_repository_work_dashboard_assignment_action_rejects_stale_node_id(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    local_node = _node()
    issue = _issue(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: local_node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "assignment_node": "999999",
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assert response.status_code == 302
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_repository_work_dashboard_active_patchwork_badge(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Active here" in content
    assert 'value="authorize-local-patchwork"' not in content


@pytest.mark.django_db
def test_repository_work_dashboard_unmarked_control_assignment_can_be_reauthorized(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node("gway-001", role=_role("Terminal"))
    RepositoryWorkNodeSnapshot.objects.create(
        node=node,
        capabilities={
            "node_role": "Control",
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    )
    pull_request = _pull_request(repository)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=True,
        reason="Legacy active assignment.",
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "")

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Active here" not in content
    assert 'value="authorize-local-patchwork"' in content


@pytest.mark.django_db
def test_repository_work_dashboard_uses_live_role_for_unmarked_control_assignment(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node("gway-001", role=_role("Terminal"))
    pull_request = _pull_request(repository)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
        patchwork_authorized=True,
        reason="Legacy active assignment.",
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)
    monkeypatch.setattr(dashboard, "local_node_role", lambda: "Control")

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Active here" not in content
    assert 'value="authorize-local-patchwork"' in content


@pytest.mark.django_db
def test_repository_work_dashboard_removes_local_assignment(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        assigned_by=user,
    )
    stale_updated_at = timezone.now() - timedelta(days=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        updated_at=stale_updated_at,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "remove-local-assignment",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED
    assert assignment.updated_at > stale_updated_at


@pytest.mark.django_db
def test_repository_work_dashboard_change_assignment_permission_can_remove(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "change_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "remove-local-assignment",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED


@pytest.mark.django_db
def test_repository_work_dashboard_authorize_local_patchwork_requires_permission(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 403
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED


@pytest.mark.django_db
def test_repository_work_dashboard_authorize_patchwork_requires_existing_assignment(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "change_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "authorize-local-patchwork",
        },
    )

    assert response.status_code == 302
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_repository_work_dashboard_assign_local_requires_assignment_permission(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assert response.status_code == 403
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_repository_work_dashboard_assign_local_requires_change_for_existing_assignment(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "add_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 403
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE


@pytest.mark.django_db
def test_repository_work_dashboard_assign_local_allows_add_only_removed_assignment(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
        "add_repositoryworkassignment",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "action": "assign-local",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 302
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment.assigned_by == user


@pytest.mark.django_db
def test_repository_work_dashboard_remove_local_assignment_requires_permission(
    client,
    monkeypatch,
):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
    )
    client.force_login(user)
    repository = _repository()
    node = _node()
    pull_request = _pull_request(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
    )
    stale_updated_at = timezone.now() - timedelta(days=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        updated_at=stale_updated_at,
    )
    monkeypatch.setattr(dashboard.Node, "get_local", lambda: node)

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "action": "remove-local-assignment",
        },
    )

    assignment.refresh_from_db()
    assert response.status_code == 403
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment.updated_at == stale_updated_at


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_invalid_utf8(client, settings):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=b"\xff\xfe",
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid json"}


@pytest.mark.django_db
def test_repository_work_dashboard_selects_configured_repository(client):
    user = _staff_user()
    client.force_login(user)
    first_repository = _repository(owner="octo", name="first")
    second_repository = _repository(owner="octo", name="second")
    first_issue = _issue(first_repository, number=11, title="First issue")
    second_issue = _issue(second_repository, number=12, title="Second issue")

    response = client.get(
        reverse("repos:repository-work-dashboard"),
        {"repository": second_repository.pk},
    )

    assert response.status_code == 200
    assert response.context["selected_repository"] == second_repository
    content = response.content.decode()
    assert first_issue.title not in content
    assert second_issue.title in content


@pytest.mark.django_db
def test_repository_work_dashboard_requires_repo_view_permissions(client):
    user = get_user_model().objects.create_user(
        username="limited-staff",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_repository_work_dashboard_hides_sync_for_view_only_staff(client):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
    )
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository)

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert issue.title in content
    assert "Sync open work" not in content
    assert 'value="add-label"' not in content


@pytest.mark.django_db
def test_repository_work_dashboard_only_shows_open_work(client):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    open_issue = _issue(repository, number=21, title="Open issue")
    closed_issue = _issue(repository, number=22, title="Closed issue", state="closed")
    open_pull_request = _pull_request(repository, number=23, title="Open PR")
    closed_pull_request = _pull_request(
        repository,
        number=24,
        title="Closed PR",
        state="closed",
    )

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert open_issue.title in content
    assert open_pull_request.title in content
    assert closed_issue.title not in content
    assert closed_pull_request.title not in content


@pytest.mark.django_db
def test_repository_work_dashboard_invalid_sync_repository_does_not_sync(
    client,
    monkeypatch,
):
    user = _staff_user()
    client.force_login(user)
    _repository()
    calls = []

    def fake_issue_fetch(*, repository, token, state):
        calls.append(("issue", repository, token, state))
        return 0, 0

    def fake_pull_request_fetch(*, repository, token, state):
        calls.append(("pull_request", repository, token, state))
        return 0, 0

    monkeypatch.setattr(
        "apps.repos.views.dashboard._sync_issues_from_github",
        fake_issue_fetch,
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard._sync_pull_requests_from_github",
        fake_pull_request_fetch,
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {"repository": "999999", "action": "sync"},
    )

    assert response.status_code == 302
    assert response.url == reverse("repos:repository-work-dashboard")
    assert calls == []


@pytest.mark.django_db
def test_repository_work_dashboard_syncs_all_work_with_user_token(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    calls = {}

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.resolve_configured_token",
        lambda *, user: "stored-token",
    )

    def fake_issue_fetch(*, repository, token, state):
        calls["issue"] = (repository, token, state)
        return 1, 2

    def fake_pull_request_fetch(*, repository, token, state):
        calls["pull_request"] = (repository, token, state)
        return 3, 4

    monkeypatch.setattr(
        "apps.repos.views.dashboard._sync_issues_from_github",
        fake_issue_fetch,
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard._sync_pull_requests_from_github",
        fake_pull_request_fetch,
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {"repository": repository.pk, "action": "sync"},
    )

    assert response.status_code == 302
    assert response.url == (
        f"{reverse('repos:repository-work-dashboard')}?repository={repository.pk}"
    )
    assert calls == {
        "issue": (repository, "stored-token", "all"),
        "pull_request": (repository, "stored-token", "all"),
    }


@pytest.mark.django_db
def test_repository_work_dashboard_shows_labels_for_stored_work(client):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    _issue(repository, labels=["bug", "triage"])
    _pull_request(repository, labels=["review"])

    response = client.get(reverse("repos:repository-work-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "bug" in content
    assert "triage" in content
    assert "review" in content
    assert 'value="add-label"' in content
    assert 'value="remove-label"' in content


@pytest.mark.django_db
def test_repository_work_dashboard_adds_known_issue_label(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository, labels=["bug"])
    calls = {}

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.resolve_configured_token",
        lambda *, user: "stored-token",
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.fetch_repository_labels",
        lambda **kwargs: [{"name": "bug"}, {"name": "triage"}],
    )

    def fake_add_issue_labels(**kwargs):
        calls["add"] = kwargs
        return object()

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.add_issue_labels",
        fake_add_issue_labels,
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "label": "triage",
            "action": "add-label",
        },
    )

    issue.refresh_from_db()
    assert response.status_code == 302
    assert issue.labels == ["bug", "triage"]
    assert calls["add"] == {
        "owner": repository.owner,
        "repository": repository.name,
        "issue_number": issue.number,
        "token": "stored-token",
        "labels": ("triage",),
    }


@pytest.mark.django_db
def test_repository_work_dashboard_replaces_existing_priority_label(
    client, monkeypatch
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository, labels=["bug"])
    calls: dict[str, list[dict[str, object]] | dict[str, object]] = {
        "remove": [],
    }

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.resolve_configured_token",
        lambda *, user: "stored-token",
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.fetch_repository_labels",
        lambda **kwargs: [
            {"name": "bug"},
            {"name": "priority: critical"},
            {"name": "priority: low"},
            {"name": "priority: high"},
        ],
    )

    def fake_remove_issue_label(**kwargs):
        calls["remove"].append(kwargs)
        return object()

    def fake_add_issue_labels(**kwargs):
        calls["add"] = kwargs
        return object()

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.remove_issue_label",
        fake_remove_issue_label,
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.add_issue_labels",
        fake_add_issue_labels,
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "label": "priority: high",
            "action": "add-label",
        },
    )

    issue.refresh_from_db()
    assert response.status_code == 302
    assert issue.labels == ["bug", "priority: high"]
    assert calls["remove"] == [
        {
            "owner": repository.owner,
            "repository": repository.name,
            "issue_number": issue.number,
            "token": "stored-token",
            "label": "priority: critical",
            "ignore_missing": True,
        },
        {
            "owner": repository.owner,
            "repository": repository.name,
            "issue_number": issue.number,
            "token": "stored-token",
            "label": "priority: low",
            "ignore_missing": True,
        },
    ]
    assert calls["add"] == {
        "owner": repository.owner,
        "repository": repository.name,
        "issue_number": issue.number,
        "token": "stored-token",
        "labels": ("priority: high",),
    }


@pytest.mark.django_db
def test_repository_work_dashboard_removes_known_pull_request_label(
    client, monkeypatch
):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    pull_request = _pull_request(repository, labels=["bug", "triage"])
    calls = {}

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.resolve_configured_token",
        lambda *, user: "stored-token",
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.fetch_repository_labels",
        lambda **kwargs: pytest.fail("remove-label should not fetch repository labels"),
    )

    def fake_remove_issue_label(**kwargs):
        calls["remove"] = kwargs
        return object()

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.remove_issue_label",
        fake_remove_issue_label,
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.PULL_REQUEST,
            "number": pull_request.number,
            "label": "bug",
            "action": "remove-label",
        },
    )

    pull_request.refresh_from_db()
    assert response.status_code == 302
    assert pull_request.labels == ["triage"]
    assert calls["remove"] == {
        "owner": repository.owner,
        "repository": repository.name,
        "issue_number": pull_request.number,
        "token": "stored-token",
        "label": "bug",
    }


@pytest.mark.django_db
def test_repository_work_dashboard_store_label_update_handles_non_list_labels():
    repository = _repository()
    issue = _issue(repository)
    issue.labels = None

    dashboard._store_label_update(issue, label="triage", action="add-label")

    issue.refresh_from_db()
    assert issue.labels == ["triage"]


@pytest.mark.django_db
def test_repository_work_dashboard_rejects_unknown_label(client, monkeypatch):
    user = _staff_user()
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository, labels=["bug"])
    calls = []

    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.resolve_configured_token",
        lambda *, user: "stored-token",
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.fetch_repository_labels",
        lambda **kwargs: [{"name": "bug"}],
    )
    monkeypatch.setattr(
        "apps.repos.views.dashboard.github_service.add_issue_labels",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "label": "unknown",
            "action": "add-label",
        },
    )

    issue.refresh_from_db()
    message_text = " ".join(
        str(message) for message in get_messages(response.wsgi_request)
    )
    assert response.status_code == 302
    assert issue.labels == ["bug"]
    assert calls == []
    assert "not configured" in message_text


@pytest.mark.django_db
def test_repository_work_dashboard_label_actions_require_change_permissions(client):
    user = _staff_user_with_repo_permissions(
        "view_githubrepository",
        "view_repositoryissue",
        "view_repositorypullrequest",
    )
    client.force_login(user)
    repository = _repository()
    issue = _issue(repository, labels=["bug"])

    response = client.post(
        reverse("repos:repository-work-dashboard"),
        {
            "repository": repository.pk,
            "target_type": GitHubMonitorTask.TargetType.ISSUE,
            "number": issue.number,
            "label": "triage",
            "action": "add-label",
        },
    )

    assert response.status_code == 403
