from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.nodes.models import Node, NodeFeature, NodeRole
from apps.repos.models import (
    GitHubRepository,
    RepositoryIssue,
    RepositoryPullRequest,
    RepositoryWorkAssignment,
    RepositoryWorkNodeSnapshot,
)
from apps.repos.services import work_assignments


def _role(name="Control"):
    return NodeRole.objects.create(name=name, acronym=name[:4].upper())


def _node(hostname="gway-001", *, role=None, relation=Node.Relation.SELF):
    return Node.objects.create(
        hostname=hostname,
        public_endpoint=hostname,
        role=role or _role(),
        current_relation=relation,
    )


def _repository():
    return GitHubRepository.objects.create(owner="arthexis", name="arthexis")


def _issue(repository, number=8731, title="Improve install smoke"):
    now = timezone.now()
    return RepositoryIssue.objects.create(
        repository=repository,
        number=number,
        title=title,
        state="open",
        labels=["automation"],
        html_url=(
            f"https://github.example/{repository.owner}/"
            f"{repository.name}/issues/{number}"
        ),
        created_at=now,
        updated_at=now,
    )


def _pull_request(repository, number=8733, title="Improve PR workflow"):
    now = timezone.now()
    return RepositoryPullRequest.objects.create(
        repository=repository,
        number=number,
        title=title,
        state="open",
        labels=["automation"],
        html_url=(
            f"https://github.example/{repository.owner}/"
            f"{repository.name}/pull/{number}"
        ),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.django_db
def test_local_developer_snapshot_includes_capacity_and_assignment_load(monkeypatch):
    node = _node()
    repository = _repository()
    issue = _issue(repository)
    removed_issue = _issue(repository, number=8732, title="Remove stale assignment")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=removed_issue.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.setattr(work_assignments, "local_node_role", lambda: "Control")

    payload = work_assignments.local_developer_snapshot()

    assert payload["schema_version"] == 1
    assert payload["node"]["hostname"] == node.hostname
    assert payload["capabilities"]["node_role"] == "Control"
    assert payload["current_load"]["assigned_work"] == 1
    assert payload["current_load"]["active_patchwork"] == 1


@pytest.mark.django_db
def test_local_developer_snapshot_prefers_live_role_over_stale_node_role(monkeypatch):
    node = _node(role=_role("Terminal"))
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.setattr(work_assignments, "local_node_role", lambda: "Control")

    payload = work_assignments.local_developer_snapshot()

    assert payload["capabilities"]["node_role"] == "Control"
    assert payload["developer_info"]["node_role"] == "Control"


@pytest.mark.django_db
def test_local_developer_snapshot_excludes_removed_assignment_load(monkeypatch):
    node = _node()
    repository = _repository()
    assigned_issue = _issue(repository)
    removed_issue = _issue(repository, number=8732)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=assigned_issue.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=removed_issue.number,
        node=node,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)

    payload = work_assignments.local_developer_snapshot()

    assert payload["current_load"]["assigned_work"] == 1


@pytest.mark.django_db
def test_local_developer_snapshot_handles_missing_load_average(monkeypatch):
    node = _node()
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.delattr(work_assignments.os, "getloadavg", raising=False)

    payload = work_assignments.local_developer_snapshot()

    assert payload["current_load"]["load_average"]["one"] == 0.0
    assert payload["current_load"]["load_average"]["five"] == 0.0
    assert payload["current_load"]["load_average"]["fifteen"] == 0.0
    assert payload["current_load"]["load_average"]["cpu_count"] >= 1


@pytest.mark.django_db
def test_assignment_sync_endpoint_records_downstream_and_returns_target_assignments(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve Control RFID scanner")
    removed_issue = _issue(repository, number=8732, title="Remove stale assignment")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=removed_issue.number,
        node=downstream,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": ["rfid", "usb"],
        },
        "current_load": {"active_patchwork": 1},
        "developer_info": {"base_dir": "/home/arthe/arthexis"},
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    body = response.json()
    returned_assignments = {
        assignment["number"]: assignment for assignment in body["assignments"]
    }
    assert (
        returned_assignments[issue.number]["status"]
        == RepositoryWorkAssignment.Status.ASSIGNED
    )
    assert (
        returned_assignments[removed_issue.number]["status"]
        == RepositoryWorkAssignment.Status.REMOVED
    )
    assert returned_assignments[issue.number]["node_fit"]["eligible"] is True
    assert returned_assignments[issue.number]["node_fit"]["matchedCapabilities"] == [
        "rfid"
    ]
    assert body["node"]["uuid"] == str(downstream.uuid)
    snapshot = RepositoryWorkNodeSnapshot.objects.get(node=downstream)
    assert snapshot.capabilities["node_features"] == ["rfid", "usb"]
    assert snapshot.current_load["active_patchwork"] == 1
    assert snapshot.developer_info["base_dir"] == "/home/arthe/arthexis"


@pytest.mark.django_db
def test_assignment_sync_endpoint_marks_generic_control_work_as_mismatch(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve install smoke")
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["classification"] == "generic-mismatch"
    assert (
        "control-role-requires-hardware-or-rpi-fit" in returned["node_fit"]["reasons"]
    )
    assignment.refresh_from_db()
    assert assignment.status == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment.patchwork_authorized is True


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_display_work_without_capabilities(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Display dashboard labels")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["matchedCapabilities"] == []


@pytest.mark.django_db
def test_assignment_sync_endpoint_does_not_use_suite_features_as_node_capability(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve USB polling")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": ["usb-inventory", "rfid-auth-audit"],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["matchedCapabilities"] == []


@pytest.mark.django_db
def test_assignment_sync_endpoint_requires_capability_for_hardware_role_terms(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve RFID scanner")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["affectedRoles"] == ["Control"]
    assert returned["node_fit"]["matchedHardware"] == []


@pytest.mark.django_db
def test_assignment_sync_endpoint_requires_hardware_fit_despite_role_term(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Fix GWAY RFID scanner")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert "gway" in returned["node_fit"]["matchedRoleTerms"]
    assert returned["node_fit"]["matchedHardware"] == []


@pytest.mark.django_db
def test_assignment_sync_endpoint_keeps_gway_platform_work_report_only_by_default(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Show GWAY PR queue")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["matchedRoleTerms"] == ["gway"]
    assert returned["node_fit"]["matchedHardware"] == []
    assert returned["node_fit"]["patchworkAuthorization"] == "manual-control-required"
    assert (
        "control-patchwork-requires-operator-authorization"
        in returned["node_fit"]["reasons"]
    )


@pytest.mark.django_db
def test_assignment_sync_endpoint_preserves_manual_control_patchwork_authorization(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Show GWAY PR queue")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        reason=work_assignments.control_manual_patchwork_reason(
            "Manual operator assignment."
        ),
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is True
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["matchedRoleTerms"] == ["gway"]


@pytest.mark.django_db
def test_assignment_sync_endpoint_treats_missing_capabilities_as_unavailable(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve RFID scanner")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["classification"] == "capabilities-not-evaluated"


@pytest.mark.django_db
@pytest.mark.django_db
def test_assignment_sync_endpoint_maps_lcd_feature_to_display_hardware(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Fix screen rotation")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": ["lcd-screen"],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["matchedHardware"] == ["display"]


@pytest.mark.django_db
def test_assignment_sync_endpoint_allows_explicit_control_role_work(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Improve control-node behavior")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["classification"] == "control-fit"
    assert returned["node_fit"]["matchedCapabilities"] == []
    assert "control-fit-role" in returned["node_fit"]["reasons"]


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_ambiguous_control_text_without_capability(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Update access control labels")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["matchedRoleTerms"] == []


@pytest.mark.django_db
def test_assignment_sync_endpoint_allows_charger_control_role_work(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Fix charger reset handling")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert returned["node_fit"]["matchedRoleTerms"] == ["charger"]


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_bare_ocpp_term_without_control_signal(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title="Fix OCPP routes")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is False
    assert returned["node_fit"]["matchedRoleTerms"] == ["ocpp"]


@pytest.mark.parametrize(
    ("title", "expected_term"),
    (
        ("Improve ocpp201 handlers", "ocpp201"),
        ("Improve OCPP 1.6 coverage", "ocpp16"),
        ("Improve OCPP 2.0.1 handlers", "ocpp201"),
    ),
)
@pytest.mark.django_db
def test_assignment_sync_endpoint_allows_versioned_ocpp_control_role_work(
    client,
    settings,
    title,
    expected_term,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository, title=title)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
        "capabilities": {
            "node_role": role.name,
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    [returned] = response.json()["assignments"]
    assert returned["number"] == issue.number
    assert returned["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert returned["patchwork_authorized"] is False
    assert returned["node_fit"]["eligible"] is True
    assert expected_term in returned["node_fit"]["matchedRoleTerms"]


@pytest.mark.django_db
def test_assignments_for_node_uses_bulk_prefetched_work_items(monkeypatch):
    node = _node(role=_role("Worker"))
    repository = _repository()
    issue = _issue(repository, title="Improve install smoke")
    pull_request = _pull_request(repository, title="Improve PR workflow")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(
        work_assignments,
        "_work_item_for_assignment",
        lambda assignment: pytest.fail("expected prefetched work item"),
    )

    assignments = work_assignments.assignments_for_node(node)

    assignments_by_number = {
        assignment["number"]: assignment for assignment in assignments
    }
    assert assignments_by_number[issue.number]["title"] == issue.title
    assert assignments_by_number[issue.number]["target_type"] == "issue"
    assert assignments_by_number[pull_request.number]["title"] == pull_request.title
    assert assignments_by_number[pull_request.number]["target_type"] == "pr"


@pytest.mark.django_db
def test_assignments_for_node_keeps_generic_pr_when_paths_are_unavailable():
    node = _node(role=_role("Control"))
    repository = _repository()
    pull_request = _pull_request(repository, title="Fix failing tests")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": ["rfid-scanner"],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["number"] == pull_request.number
    assert assignment["target_type"] == "pr"
    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["classification"] == "pr-metadata-not-evaluated"
    assert "pr-paths-unavailable" in assignment["node_fit"]["reasons"]


@pytest.mark.django_db
def test_assignments_for_node_ignores_control_marker_for_generic_pr_fit():
    node = _node(role=_role("Control"))
    repository = _repository()
    pull_request = _pull_request(repository, title="Fix failing tests")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.PULL_REQUEST,
        number=pull_request.number,
        node=node,
        patchwork_authorized=True,
        reason=work_assignments.control_manual_patchwork_reason("Operator approved."),
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": ["rfid-scanner"],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["number"] == pull_request.number
    assert assignment["status"] == RepositoryWorkAssignment.Status.ACTIVE
    assert assignment["patchwork_authorized"] is True
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["classification"] == "pr-metadata-not-evaluated"
    assert assignment["node_fit"]["matchedRoles"] == []


@pytest.mark.django_db
def test_assignments_for_node_omits_labels_without_cached_work_item():
    node = _node(role=_role("Worker"))
    repository = _repository()
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=9001,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(node)

    assert assignment["number"] == 9001
    assert assignment["target_type"] == "issue"
    assert "labels" not in assignment


@pytest.mark.django_db
def test_assignments_for_node_keeps_assignment_when_control_metadata_is_missing():
    node = _node(role=_role("Control"))
    repository = _repository()
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=8731,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": ["rfid-scanner"],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["classification"] == "target-metadata-unavailable"


@pytest.mark.django_db
def test_assignments_for_node_prefers_reported_capability_role_over_stored_role():
    node = _node(role=_role("Terminal"))
    repository = _repository()
    issue = _issue(repository, title="Improve generic docs")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["nodeRole"] == "Control"
    assert assignment["node_fit"]["classification"] == "generic-mismatch"


@pytest.mark.django_db
def test_assignments_for_node_uses_summary_feature_aliases():
    node = _node(role=_role("Control"))
    repository = _repository()
    issue = _issue(repository, title="Fix LLM summary generation")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": ["llm-summary"],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["matchedCapabilities"] == ["llm-summary", "summary"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "title",
    (
        "Configure --ocpp-gateway route",
        "Configure ocpp_gateway route",
    ),
)
def test_assignments_for_node_allows_explicit_ocpp_gateway_work(title):
    node = _node(role=_role("Control"))
    repository = _repository()
    issue = _issue(repository, title=title)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": [],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert "ocpp-gateway" in assignment["node_fit"]["matchedRoleTerms"]


@pytest.mark.django_db
def test_assignments_for_node_matches_exact_reported_feature_slugs():
    node = _node(role=_role("Control"))
    repository = _repository()
    issue = _issue(repository, title="Fix Kindle Postbox sync")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities={
            "node_role": "Control",
            "node_features": ["kindle-postbox"],
            "suite_features": [],
            "capability_terms": [],
        },
    )

    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["matchedCapabilities"] == ["kindle-postbox"]


@pytest.mark.django_db
def test_assignments_for_node_skips_fit_when_capabilities_are_unavailable():
    node = _node(role=_role("Control"))
    repository = _repository()
    issue = _issue(repository, title="Improve RFID scanner")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )

    [assignment] = work_assignments.assignments_for_node(node)

    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["classification"] == "capabilities-not-evaluated"


@pytest.mark.django_db
def test_assignments_for_node_uses_current_capability_aliases(monkeypatch):
    node = _node(role=_role("Control"))
    lcd_feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")
    node.features.add(lcd_feature)
    repository = _repository()
    issue = _issue(repository, title="Improve LCD display")
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=node,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ASSIGNED,
    )
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.setattr(work_assignments, "local_node_role", lambda: "Control")

    snapshot = work_assignments.local_developer_snapshot()
    [assignment] = work_assignments.assignments_for_node(
        node,
        capabilities=snapshot["capabilities"],
    )

    assert snapshot["capabilities"]["capability_terms"] == [
        "display",
        "lcd",
        "lcd-screen",
    ]
    assert assignment["status"] == RepositoryWorkAssignment.Status.ASSIGNED
    assert assignment["patchwork_authorized"] is False
    assert assignment["node_fit"]["eligible"] is True
    assert assignment["node_fit"]["classification"] == "control-fit"
    assert assignment["node_fit"]["matchedHardware"] == ["display"]


@pytest.mark.django_db
def test_assignment_sync_endpoint_accepts_trusted_downstream_node(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    downstream = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    downstream.trusted = True
    downstream.save()
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "public_endpoint": downstream.public_endpoint,
            "uuid": str(downstream.uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    assert response.json()["node"]["uuid"] == str(downstream.uuid)
    assert RepositoryWorkNodeSnapshot.objects.get().node == downstream


@pytest.mark.django_db
def test_assignment_sync_endpoint_returns_removed_assignment_tombstones(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    role = _role("Control")
    downstream = _node(
        "gway-001",
        role=role,
        relation=Node.Relation.DOWNSTREAM,
    )
    repository = _repository()
    issue = _issue(repository)
    RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=downstream,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": downstream.hostname,
            "uuid": str(downstream.uuid),
            "role": role.name,
            "current_relation": Node.Relation.DOWNSTREAM,
            "public_endpoint": downstream.public_endpoint,
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assignments"][0]["number"] == issue.number
    assert body["assignments"][0]["status"] == RepositoryWorkAssignment.Status.REMOVED
    assert body["assignments"][0]["node_fit"]["eligible"] is False
    assert body["assignments"][0]["node_fit"]["classification"] == "removed"


@pytest.mark.django_db
def test_assignment_sync_endpoint_requires_token(client, settings):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data="{}",
        content_type="application/json",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_assignment_sync_endpoint_treats_non_ascii_token_as_unauthorized(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data="{}",
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer \u00e9",
    )

    assert response.status_code == 401
    assert Node.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_missing_node_identity(client, settings):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data="{}",
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert Node.objects.count() == 0
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_malformed_uuid(client, settings):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    existing = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {"hostname": existing.hostname, "uuid": "not-a-uuid"},
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert Node.objects.count() == 1
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_non_object_json(client, settings):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data="[]",
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert Node.objects.count() == 0
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_does_not_match_new_uuid_by_hostname(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    existing = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    reported_uuid = uuid.uuid4()
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": existing.hostname,
            "uuid": str(reported_uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node"]["uuid"] == str(reported_uuid)
    assert Node.objects.count() == 2
    assert RepositoryWorkNodeSnapshot.objects.get().node != existing


@pytest.mark.django_db
def test_assignment_sync_endpoint_matches_hostname_case_insensitively(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    existing = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {"hostname": "GWAY-001"},
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 200
    assert response.json()["node"]["uuid"] == str(existing.uuid)
    assert Node.objects.count() == 1
    assert RepositoryWorkNodeSnapshot.objects.get().node == existing


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_conflicting_uuid_and_endpoint(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    existing = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": "new-gway",
            "public_endpoint": existing.public_endpoint,
            "uuid": str(uuid.uuid4()),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert Node.objects.count() == 1
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_matched_uuid_duplicate_endpoint(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    matched = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    endpoint_owner = _node("gway-002", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": matched.hostname,
            "public_endpoint": endpoint_owner.public_endpoint,
            "uuid": str(matched.uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    matched.refresh_from_db()
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert matched.public_endpoint == "gway-001"
    assert Node.objects.count() == 2
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_self_node_identity_poisoning(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    local = _node("local-node", relation=Node.Relation.SELF)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": "attacker-self-host",
            "public_endpoint": "attacker-self-endpoint",
            "uuid": str(local.uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    local.refresh_from_db()
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert local.hostname == "local-node"
    assert local.public_endpoint == "local-node"
    assert local.current_relation == Node.Relation.SELF
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_peer_node_reassignment(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    peer = _node("peer-node", relation=Node.Relation.PEER)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": "attacker-peer-host",
            "public_endpoint": "attacker-peer-endpoint",
            "uuid": str(peer.uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    peer.refresh_from_db()
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert peer.hostname == "peer-node"
    assert peer.public_endpoint == "peer-node"
    assert peer.current_relation == Node.Relation.PEER
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_rejects_downstream_identity_changes(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    downstream = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-06-13T18:00:00Z",
        "node": {
            "hostname": "attacker-gway",
            "public_endpoint": downstream.public_endpoint,
            "uuid": str(downstream.uuid),
        },
    }

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )

    downstream.refresh_from_db()
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid sync payload"}
    assert downstream.hostname == "gway-001"
    assert downstream.public_endpoint == "gway-001"
    assert RepositoryWorkNodeSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_assignment_sync_endpoint_falls_back_from_invalid_reported_at(
    client,
    settings,
):
    settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN = "sync-token"
    node = _node("gway-001", relation=Node.Relation.DOWNSTREAM)
    payload = {
        "schema_version": 1,
        "reported_at": "2026-13-01T00:00:00Z",
        "node": {
            "hostname": node.hostname,
            "uuid": str(node.uuid),
        },
    }
    before = timezone.now()

    response = client.post(
        reverse("repos:repository-work-assignment-sync"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_ARTHEXIS_ASSIGNMENT_TOKEN="sync-token",
    )
    after = timezone.now()

    assert response.status_code == 200
    snapshot = RepositoryWorkNodeSnapshot.objects.get(node=node)
    assert before <= snapshot.reported_at <= after


@pytest.mark.django_db
def test_pull_assignments_from_upstream_posts_developer_info_and_imports(monkeypatch):
    local = _node("gway-001")
    repository = _repository()
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: local)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "schema_version": 1,
                "node": {
                    "hostname": local.hostname,
                    "uuid": str(local.uuid),
                },
                "assignments": [
                    {
                        "repo": repository.slug,
                        "target_type": "issue",
                        "number": 8731,
                        "title": "Improve install smoke",
                        "url": "https://github.example/issues/8731",
                        "state": "open",
                        "labels": ["rfid", "control"],
                        "patchwork_authorized": True,
                        "status": "active",
                        "assigned_at": "2026-06-13T18:05:00Z",
                        "updated_at": "2026-06-13T18:10:00Z",
                    }
                ],
            }

    def post(url, *, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return Response()

    monkeypatch.setattr(work_assignments.requests, "post", post)

    result = work_assignments.pull_assignments_from_upstream(
        upstream_url="https://arthexis.example",
        token="sync-token",
        timeout=2,
    )

    assert calls[0][0] == "https://arthexis.example/repos/work/assignments/sync/"
    assert calls[0][1]["node"]["uuid"] == str(local.uuid)
    assert calls[0][2][work_assignments.ASSIGNMENT_SYNC_HEADER] == "sync-token"
    assert calls[0][3] == 2
    assert result == {
        "enabled": True,
        "url": calls[0][0],
        "created": 1,
        "updated": 0,
        "removed": 0,
    }
    assignment = RepositoryWorkAssignment.objects.get(number=8731, node=local)
    assert assignment.patchwork_authorized is True
    assert assignment.status == RepositoryWorkAssignment.Status.ACTIVE
    assert assignment.assigned_at == parse_datetime("2026-06-13T18:05:00Z")
    assert assignment.updated_at == parse_datetime("2026-06-13T18:10:00Z")
    issue = RepositoryIssue.objects.get(number=8731)
    assert issue.title == "Improve install smoke"
    assert issue.html_url == "https://github.example/issues/8731"
    assert issue.labels == ["rfid", "control"]


@pytest.mark.django_db
def test_pull_assignments_from_upstream_rejects_non_object_response(monkeypatch):
    local = _node("gway-001")
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: local)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    monkeypatch.setattr(
        work_assignments.requests,
        "post",
        lambda *args, **kwargs: Response(),
    )

    with pytest.raises(work_assignments.AssignmentSyncError):
        work_assignments.pull_assignments_from_upstream(
            upstream_url="https://arthexis.example",
            token="sync-token",
            timeout=2,
        )

    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("assignments", ["oops", {"repo": "arthexis/arthexis"}])
def test_apply_assignment_payload_rejects_non_list_assignments(assignments):
    local = _node("gway-001")

    with pytest.raises(work_assignments.AssignmentSyncError):
        work_assignments.apply_assignment_payload(
            {"schema_version": 1, "assignments": assignments},
            node=local,
        )

    assert GitHubRepository.objects.count() == 0
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_assignment_payload_removes_imported_assignments():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=local,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "status": RepositoryWorkAssignment.Status.REMOVED,
                    "updated_at": "2026-06-13T18:20:00Z",
                }
            ],
        },
        node=local,
    )

    assignment.refresh_from_db()
    assert result == {"created": 0, "updated": 0, "removed": 1}
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED
    assert assignment.updated_at == parse_datetime("2026-06-13T18:20:00Z")


@pytest.mark.django_db
def test_assignment_payload_treats_node_fit_mismatch_as_removal():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=local,
        patchwork_authorized=True,
        status=RepositoryWorkAssignment.Status.ACTIVE,
    )

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "status": RepositoryWorkAssignment.Status.ACTIVE,
                    "patchwork_authorized": True,
                    "node_fit": {
                        "eligible": False,
                        "classification": "generic-mismatch",
                    },
                    "updated_at": "2026-06-13T18:20:00Z",
                }
            ],
        },
        node=local,
    )

    assignment.refresh_from_db()
    assert result == {"created": 0, "updated": 0, "removed": 1}
    assert assignment.patchwork_authorized is False
    assert assignment.status == RepositoryWorkAssignment.Status.REMOVED
    assert assignment.updated_at == parse_datetime("2026-06-13T18:20:00Z")


@pytest.mark.django_db
def test_assignment_payload_skips_node_fit_mismatch_without_existing_assignment():
    local = _node("gway-001")

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": "arthexis/arthexis",
                    "target_type": "issue",
                    "number": 8731,
                    "status": RepositoryWorkAssignment.Status.ACTIVE,
                    "patchwork_authorized": True,
                    "node_fit": {
                        "eligible": False,
                        "classification": "generic-mismatch",
                    },
                    "updated_at": "2026-06-13T18:20:00Z",
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert GitHubRepository.objects.count() == 0
    assert RepositoryIssue.objects.count() == 0
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_assignment_payload_skips_unknown_repository_tombstones():
    local = _node("gway-001")

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": "arthexis/unknown",
                    "target_type": "issue",
                    "number": 8731,
                    "status": RepositoryWorkAssignment.Status.REMOVED,
                    "updated_at": "2026-06-13T18:20:00Z",
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert not GitHubRepository.objects.filter(
        owner="arthexis",
        name="unknown",
    ).exists()


@pytest.mark.django_db
def test_assignment_payload_skips_negative_assignment_numbers():
    local = _node("gway-001")

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": "arthexis/invalid-number",
                    "target_type": "issue",
                    "number": -1,
                    "title": "Invalid issue",
                    "status": RepositoryWorkAssignment.Status.ASSIGNED,
                    "updated_at": "2026-06-13T18:20:00Z",
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert not GitHubRepository.objects.filter(
        owner="arthexis",
        name="invalid-number",
    ).exists()


@pytest.mark.django_db
def test_apply_assignment_payload_preserves_upstream_assigned_at_on_create():
    local = _node("gway-001")
    repository = _repository()
    assigned_at = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    updated_at = timezone.now().replace(microsecond=0) - timedelta(hours=1)

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": 8731,
                    "title": "Improve install smoke",
                    "url": "https://github.example/issues/8731",
                    "state": "open",
                    "patchwork_authorized": True,
                    "status": "active",
                    "assigned_at": assigned_at.isoformat(),
                    "updated_at": updated_at.isoformat(),
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 1, "updated": 0, "removed": 0}
    assignment = RepositoryWorkAssignment.objects.get(number=8731, node=local)
    assert assignment.assigned_at == assigned_at
    assert assignment.updated_at == updated_at


@pytest.mark.django_db
def test_apply_assignment_payload_falls_back_from_invalid_timestamps():
    local = _node("gway-001")
    repository = _repository()
    before = timezone.now()

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": 8731,
                    "title": "Improve install smoke",
                    "url": "https://github.example/issues/8731",
                    "state": "open",
                    "patchwork_authorized": True,
                    "status": "active",
                    "assigned_at": "2026-13-01T00:00:00Z",
                    "updated_at": "2026-14-01T00:00:00Z",
                }
            ],
        },
        node=local,
    )
    after = timezone.now()

    assert result == {"created": 1, "updated": 0, "removed": 0}
    assignment = RepositoryWorkAssignment.objects.get(number=8731, node=local)
    assert before <= assignment.assigned_at <= after
    assert before <= assignment.updated_at <= after


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("reported_value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("unexpected", False),
    ],
)
def test_apply_assignment_payload_parses_patchwork_authorization_strings(
    reported_value,
    expected,
):
    local = _node("gway-001")
    repository = _repository()

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": 8731,
                    "patchwork_authorized": reported_value,
                    "status": "active",
                }
            ],
        },
        node=local,
    )

    assignment = RepositoryWorkAssignment.objects.get(number=8731, node=local)
    assert result == {"created": 1, "updated": 0, "removed": 0}
    assert assignment.patchwork_authorized is expected


@pytest.mark.django_db
def test_apply_assignment_payload_ignores_unknown_status():
    local = _node("gway-001")
    repository = _repository()

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": 8731,
                    "title": "Improve install smoke",
                    "url": "https://github.example/issues/8731",
                    "state": "open",
                    "patchwork_authorized": True,
                    "status": "unknown",
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert RepositoryIssue.objects.count() == 0
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "assignment",
    [
        {
            "repo": "new/empty",
            "target_type": "issue",
            "number": "not-an-int",
            "status": "assigned",
        },
        {
            "repo": "new/empty",
            "target_type": "issue",
            "number": 1.9,
            "status": "assigned",
        },
        {
            "repo": "new/empty",
            "target_type": "issue",
            "number": True,
            "status": "assigned",
        },
        {
            "repo": "new/empty",
            "target_type": "issue",
            "number": 8731,
            "status": "unknown",
        },
        {
            "repo": "new/empty",
            "target_type": "unsupported",
            "number": 8731,
            "status": "assigned",
        },
        {
            "repo": "new/empty",
            "number": 8731,
            "status": "assigned",
        },
        {
            "repo": "new/empty",
            "target_type": "",
            "number": 8731,
            "status": "assigned",
        },
    ],
)
def test_apply_assignment_payload_rejects_invalid_records_before_creating_repo(
    assignment,
):
    local = _node("gway-001")

    result = work_assignments.apply_assignment_payload(
        {"schema_version": 1, "assignments": [assignment]},
        node=local,
    )

    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert GitHubRepository.objects.count() == 0
    assert RepositoryIssue.objects.count() == 0
    assert RepositoryWorkAssignment.objects.count() == 0


@pytest.mark.django_db
def test_apply_assignment_payload_preserves_upstream_updated_at_on_repeat():
    local = _node("gway-001")
    repository = _repository()
    assigned_at = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    updated_at = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    payload = {
        "schema_version": 1,
        "assignments": [
            {
                "repo": repository.slug,
                "target_type": "issue",
                "number": 8731,
                "title": "Improve install smoke",
                "url": "https://github.example/issues/8731",
                "state": "open",
                "patchwork_authorized": True,
                "status": "active",
                "assigned_at": assigned_at.isoformat(),
                "updated_at": updated_at.isoformat(),
            }
        ],
    }

    result = work_assignments.apply_assignment_payload(payload, node=local)
    assert result == {"created": 1, "updated": 0, "removed": 0}
    assignment = RepositoryWorkAssignment.objects.get(number=8731, node=local)
    assert assignment.updated_at == updated_at

    result = work_assignments.apply_assignment_payload(payload, node=local)

    assignment.refresh_from_db()
    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert assignment.updated_at == updated_at


@pytest.mark.django_db
def test_apply_assignment_payload_preserves_existing_issue_metadata():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository, number=8731, title="Existing title")
    github_updated_at = timezone.now() - timedelta(days=3)
    issue.api_url = "https://api.github.example/issues/8731"
    issue.author = "octocat"
    issue.labels = ["priority: high"]
    issue.save(update_fields=["api_url", "author", "labels"])
    RepositoryIssue.objects.filter(pk=issue.pk).update(updated_at=github_updated_at)

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "title": "Assigned issue title",
                    "url": "https://github.example/issues/8731",
                    "state": "open",
                    "patchwork_authorized": True,
                    "status": "active",
                }
            ],
        },
        node=local,
    )

    assert result == {"created": 1, "updated": 0, "removed": 0}
    issue.refresh_from_db()
    assert issue.title == "Assigned issue title"
    assert issue.api_url == "https://api.github.example/issues/8731"
    assert issue.author == "octocat"
    assert issue.labels == ["priority: high"]
    assert issue.updated_at == github_updated_at


@pytest.mark.django_db
def test_apply_assignment_payload_updates_serialized_issue_labels():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository)

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "title": "Improve install smoke",
                    "labels": ["rfid", {"name": "Control"}],
                    "patchwork_authorized": True,
                    "status": "assigned",
                    "updated_at": "2026-06-13T18:10:00Z",
                }
            ],
        },
        node=local,
    )

    issue.refresh_from_db()
    assert result == {"created": 1, "updated": 0, "removed": 0}
    assert issue.labels == ["rfid", "Control"]


@pytest.mark.django_db
def test_apply_assignment_payload_preserves_existing_issue_fields_when_empty():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository, number=8731, title="Existing title")
    issue.state = "closed"
    issue.html_url = "https://github.example/issues/8731"
    issue.labels = ["priority: high"]
    issue.save(update_fields=["state", "html_url", "labels"])
    github_updated_at = timezone.now() - timedelta(days=3)
    RepositoryIssue.objects.filter(pk=issue.pk).update(updated_at=github_updated_at)

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "title": "",
                    "state": "",
                    "url": "",
                    "patchwork_authorized": True,
                    "status": "active",
                }
            ],
        },
        node=local,
    )

    issue.refresh_from_db()
    assert result == {"created": 1, "updated": 0, "removed": 0}
    assert issue.title == "Existing title"
    assert issue.state == "closed"
    assert issue.html_url == "https://github.example/issues/8731"
    assert issue.labels == ["priority: high"]
    assert issue.updated_at == github_updated_at


@pytest.mark.django_db
def test_apply_assignment_payload_does_not_refresh_existing_removal():
    local = _node("gway-001")
    repository = _repository()
    issue = _issue(repository)
    assignment = RepositoryWorkAssignment.objects.create(
        repository=repository,
        target_type=RepositoryWorkAssignment.TargetType.ISSUE,
        number=issue.number,
        node=local,
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
    )
    stale_updated_at = timezone.now() - timedelta(days=1)
    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
        updated_at=stale_updated_at,
    )

    result = work_assignments.apply_assignment_payload(
        {
            "schema_version": 1,
            "assignments": [
                {
                    "repo": repository.slug,
                    "target_type": "issue",
                    "number": issue.number,
                    "patchwork_authorized": False,
                    "status": "removed",
                    "updated_at": timezone.now().isoformat(),
                }
            ],
        },
        node=local,
    )

    assignment.refresh_from_db()
    assert result == {"created": 0, "updated": 0, "removed": 0}
    assert assignment.updated_at == stale_updated_at


@pytest.mark.django_db
def test_local_developer_snapshot_tolerates_missing_patchwork_disk(monkeypatch):
    node = _node("gway-001")
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.setenv("ARTHEXIS_PATCHWORK_DIR", "/tmp/arthexis-missing-patchwork")

    payload = work_assignments.local_developer_snapshot()

    assert payload["current_load"]["patchwork_disk"] == {}


@pytest.mark.django_db
def test_local_developer_snapshot_uses_shared_patchwork_dir(monkeypatch, tmp_path):
    node = _node("gway-001")
    patchwork = tmp_path / "patchwork-root"
    patchwork.mkdir()
    monkeypatch.setattr(work_assignments.Node, "get_local", lambda: node)
    monkeypatch.setattr(work_assignments, "resolve_patchwork_dir", lambda: patchwork)

    payload = work_assignments.local_developer_snapshot()

    assert payload["capabilities"]["patchwork_dir"] == str(patchwork)
    assert payload["current_load"]["patchwork_disk"]["path"] == str(patchwork)


def test_upstream_assignment_pull_runs_every_two_minutes():
    entry = settings.CELERY_BEAT_SCHEDULE["repository_work_assignment_upstream_pull"]

    assert entry["task"] == "apps.repos.tasks.pull_upstream_repository_assignments"
    assert entry["schedule"] == timedelta(minutes=2)
