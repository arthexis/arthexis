from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from apps.nodes.models import Node, NodeRole
from apps.repos.admin import RepositoryWorkAssignmentAdmin
from apps.repos.models import GitHubRepository, RepositoryWorkAssignment


def _role(name="Control"):
    return NodeRole.objects.create(name=name, acronym=name[:4].upper())


def _node(hostname="gway-001", *, role=None):
    return Node.objects.create(
        hostname=hostname,
        public_endpoint=hostname,
        role=role or _role(),
    )


def _repository(owner="arthexis", name="arthexis"):
    return GitHubRepository.objects.create(owner=owner, name=name)


@pytest.mark.django_db
def test_assignment_admin_retarget_preserves_removed_tombstone():
    old_repository = _repository()
    new_repository = _repository(name="ops")
    old_node = _node("gway-001")
    new_node = _node("gway-002")
    user = get_user_model().objects.create_superuser(username="repo-work-admin")
    assignment = RepositoryWorkAssignment.objects.create(
        repository=old_repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=8733,
        node=old_node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
        assigned_by=user,
        reason="Original assignment.",
    )
    assigned_at = timezone.now() - timedelta(hours=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        assigned_at=assigned_at,
        updated_at=assigned_at,
    )
    assignment.refresh_from_db()

    request = RequestFactory().post("/admin/repos/repositoryworkassignment/")
    request.user = user
    model_admin = RepositoryWorkAssignmentAdmin(RepositoryWorkAssignment, admin.site)
    assignment.repository = new_repository
    assignment.target_type = RepositoryWorkAssignment.TargetType.PULL_REQUEST
    assignment.number = 8734
    assignment.node = new_node

    model_admin.save_model(request, assignment, form=None, change=True)

    assignment.refresh_from_db()
    tombstone = RepositoryWorkAssignment.objects.get(
        repository=old_repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=8733,
        node=old_node,
    )
    assert assignment.repository == new_repository
    assert assignment.target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST
    assert assignment.number == 8734
    assert assignment.node == new_node
    assert tombstone.pk != assignment.pk
    assert tombstone.patchwork_authorized is False
    assert tombstone.status == RepositoryWorkAssignment.Status.REMOVED
    assert tombstone.assigned_by == user
    assert tombstone.assigned_at == assigned_at
    assert tombstone.updated_at > assigned_at


@pytest.mark.django_db
def test_assignment_admin_delete_model_marks_removed_tombstone():
    repository = _repository()
    node = _node()
    user = get_user_model().objects.create_superuser(username="delete-admin")
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=8733,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
        assigned_by=user,
    )
    stale_updated_at = timezone.now() - timedelta(hours=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        updated_at=stale_updated_at,
    )
    assignment.refresh_from_db()
    request = RequestFactory().post("/admin/repos/repositoryworkassignment/")
    request.user = user
    model_admin = RepositoryWorkAssignmentAdmin(RepositoryWorkAssignment, admin.site)

    model_admin.delete_model(request, assignment)

    assignment.refresh_from_db()
    assert RepositoryWorkAssignment.objects.count() == 1
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED
    assert assignment.updated_at > stale_updated_at


@pytest.mark.django_db
def test_assignment_admin_delete_queryset_marks_removed_tombstones():
    repository = _repository()
    node = _node()
    user = get_user_model().objects.create_superuser(username="bulk-delete-admin")
    assignments = [
        RepositoryWorkAssignment.objects.create(
            repository=repository,
            target_type=RepositoryWorkAssignment.TargetType.ISSUE,
            number=number,
            node=node,
            patchwork_authorized=True,
            status=RepositoryWorkAssignment.Status.ACTIVE,
            assigned_by=user,
        )
        for number in (8733, 8734)
    ]
    request = RequestFactory().post("/admin/repos/repositoryworkassignment/")
    request.user = user
    model_admin = RepositoryWorkAssignmentAdmin(RepositoryWorkAssignment, admin.site)

    model_admin.delete_queryset(
        request,
        RepositoryWorkAssignment.objects.filter(
            pk__in=[assignment.pk for assignment in assignments]
        ),
    )

    assert RepositoryWorkAssignment.objects.count() == 2
    assert set(
        RepositoryWorkAssignment.objects.values_list(
            "status",
            "patchwork_authorized",
        )
    ) == {(RepositoryWorkAssignment.Status.REMOVED, False)}
