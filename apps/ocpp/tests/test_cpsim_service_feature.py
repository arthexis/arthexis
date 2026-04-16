"""Regression tests for OCPP Simulator suite feature control."""

from datetime import datetime

from django.urls import reverse

import pytest

from apps.features.models import Feature
from apps.nodes.models import Node
from apps.ocpp.cpsim_service import (
    CPSIM_FEATURE_SLUG,
    CPSIM_START_QUEUED_STATUS,
    cpsim_service_enabled,
    get_cpsim_request_metadata,
    is_cpsim_start_queued_status,
    queue_cpsim_request,
)


pytestmark = pytest.mark.django_db


def test_cpsim_service_enabled_reads_suite_feature_flag():
    """Regression: service enabled state should come from suite feature flag."""

    Feature.objects.update_or_create(
        slug=CPSIM_FEATURE_SLUG,
        defaults={"display": "OCPP Simulator", "is_enabled": True},
    )
    assert cpsim_service_enabled() is True


def test_simulator_admin_toggle_updates_suite_feature(admin_client, monkeypatch):
    """Regression: admin toggle should update suite feature instead of node feature assignment."""

    feature, _ = Feature.objects.update_or_create(
        slug=CPSIM_FEATURE_SLUG,
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )
    node = Node.objects.create(hostname="sim-node", public_endpoint="sim-node")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    response = admin_client.post(reverse("admin:ocpp_simulator_cpsim_toggle"))

    assert response.status_code == 302
    feature.refresh_from_db()
    assert feature.is_enabled is True


def test_simulator_admin_toggle_requires_local_node(admin_client, monkeypatch):
    """Regression: admin toggle should fail gracefully when no local node is registered."""

    Feature.objects.update_or_create(
        slug=CPSIM_FEATURE_SLUG,
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: None))

    response = admin_client.post(reverse("admin:ocpp_simulator_cpsim_toggle"), follow=True)

    assert response.status_code == 200
    messages = [str(message) for message in response.context["messages"]]
    assert any("No local node is registered" in message for message in messages)


def test_get_cpsim_request_metadata_returns_unqueued_when_absent(tmp_path):
    metadata = get_cpsim_request_metadata(base_dir=tmp_path)

    assert metadata["queued"] is False
    assert metadata["lock_path"].endswith("cpsim-service.lck")


def test_get_cpsim_request_metadata_reports_age_for_existing_queue(tmp_path):
    queue_cpsim_request(action="start", name="Simulator", base_dir=tmp_path)

    metadata = get_cpsim_request_metadata(base_dir=tmp_path)

    assert metadata["queued"] is True
    assert metadata["age_seconds"] >= 0
    assert isinstance(metadata["queued_at"], datetime)


def test_get_cpsim_request_metadata_handles_unreadable_lock_path(monkeypatch):
    class UnreadablePath:
        def exists(self):
            raise PermissionError("read-only")

        def __str__(self):
            return "/workspace/arthexis/.locks/cpsim-service.lck"

    monkeypatch.setattr(
        "apps.ocpp.cpsim_service._lock_path",
        lambda **_kwargs: UnreadablePath(),
    )

    metadata = get_cpsim_request_metadata()

    assert metadata == {
        "queued": False,
        "lock_path": "/workspace/arthexis/.locks/cpsim-service.lck",
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CPSIM_START_QUEUED_STATUS, True),
        ("cpsim-service start requested", True),
        ("cpsim-service start requested (awaiting worker)", True),
        ("running", False),
        (None, False),
    ],
)
def test_is_cpsim_start_queued_status_supports_legacy_prefix(status, expected):
    assert is_cpsim_start_queued_status(status) is expected
