"""Regression tests for AWS admin load-instance actions."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import LightsailFetchError


pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _fake_instance_payload(name: str, *, ip: str) -> dict[str, object]:
    """Build a minimal Lightsail instance payload for consolidation tests."""

    return {
        "name": name,
        "location": {"availabilityZone": "us-east-1a"},
        "state": {"name": "running"},
        "publicIpAddress": ip,
        "privateIpAddress": "10.0.0.1",
        "blueprintId": "ubuntu_22_04",
        "bundleId": "nano_3_0",
        "arn": "arn:aws:lightsail:us-east-1:1:Instance/test",
        "supportCode": "code",
        "username": "ubuntu",
        "resourceType": "Instance",
    }


def test_credentials_tool_action_load_instances_redirects(admin_client, monkeypatch):
    """Regression: credentials dashboard/changelist tool should load and redirect."""

    credentials = AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIAPRIMARY",
        secret_access_key="secret",  # noqa: S106
    )
    monkeypatch.setattr("apps.aws.admin.list_lightsail_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(
        "apps.aws.admin.list_lightsail_instances",
        lambda **kwargs: [_fake_instance_payload("app-1", ip="1.1.1.1")],
    )

    url = reverse("admin:aws_awscredentials_actions", args=["load_instances"])
    response = admin_client.post(url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:aws_lightsailinstance_changelist")
    instance = LightsailInstance.objects.get(name="app-1", region="us-east-1")
    assert instance.credentials_id == credentials.pk


def test_credentials_tool_action_rejects_get(admin_client):
    """State-changing tool action should reject GET requests."""

    url = reverse("admin:aws_awscredentials_actions", args=["load_instances"])
    response = admin_client.get(url)

    assert response.status_code == 403


def test_credentials_selected_action_loads_instances_for_each_selection(admin_client, monkeypatch):
    """Regression: selected-rows action should consolidate Lightsail instances."""

    credential = AWSCredentials.objects.create(
        name="selected",
        access_key_id="AKIASELECTED",
        secret_access_key="secret",  # noqa: S106
    )
    monkeypatch.setattr("apps.aws.admin.list_lightsail_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(
        "apps.aws.admin.list_lightsail_instances",
        lambda **kwargs: [_fake_instance_payload("worker-1", ip="2.2.2.2")],
    )

    changelist_url = reverse("admin:aws_awscredentials_changelist")
    response = admin_client.post(
        changelist_url,
        {
            "action": "load_instances_for_selected",
            "_selected_action": [str(credential.pk)],
            "index": "0",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert LightsailInstance.objects.filter(name="worker-1", region="us-east-1").exists()


def test_instance_tool_action_is_registered_and_redirects(admin_client, monkeypatch):
    """Regression: instance changelist/dashboard tool should trigger load flow."""

    AWSCredentials.objects.create(
        name="instance",
        access_key_id="AKIAINSTANCE",
        secret_access_key="secret",  # noqa: S106
    )
    monkeypatch.setattr("apps.aws.admin.list_lightsail_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(
        "apps.aws.admin.list_lightsail_instances",
        lambda **kwargs: [_fake_instance_payload("node-1", ip="3.3.3.3")],
    )

    url = reverse("admin:aws_lightsailinstance_actions", args=["load_instances"])
    response = admin_client.post(url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:aws_lightsailinstance_changelist")
    assert LightsailInstance.objects.filter(name="node-1", region="us-east-1").exists()


def test_instance_tool_action_rejects_get(admin_client):
    """Instance load tool action should reject GET requests."""

    url = reverse("admin:aws_lightsailinstance_actions", args=["load_instances"])
    response = admin_client.get(url)

    assert response.status_code == 403


def test_credentials_load_records_discovery_for_loaded_items_only(admin_client, monkeypatch):
    """Discovery records should reflect only rows processed in current run."""

    credential = AWSCredentials.objects.create(
        name="loaded",
        access_key_id="AKIALOADED",
        secret_access_key="secret",  # noqa: S106
    )
    stale = LightsailInstance.objects.create(name="stale", region="us-east-2", credentials=credential)
    monkeypatch.setattr("apps.aws.admin.list_lightsail_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(
        "apps.aws.admin.list_lightsail_instances",
        lambda **kwargs: [_fake_instance_payload("fresh", ip="4.4.4.4")],
    )

    recorded: list[tuple[str, bool, bool]] = []

    def _record(*args, **kwargs):
        obj = kwargs["obj"]
        recorded.append((obj.name, kwargs["created"], kwargs["overwritten"]))

    monkeypatch.setattr("apps.aws.admin.record_discovery_item", _record)

    url = reverse("admin:aws_awscredentials_actions", args=["load_instances"])
    response = admin_client.post(url)

    assert response.status_code == 302
    assert stale.name not in [name for name, *_ in recorded]
    assert ("fresh", True, False) in recorded


def test_credentials_load_handles_region_listing_failure(admin_client, monkeypatch):
    """Region listing failures should surface as admin error messages, not 500s."""

    AWSCredentials.objects.create(
        name="primary",
        access_key_id="AKIAPRIMARY2",
        secret_access_key="secret",  # noqa: S106
    )

    def _boom():
        raise LightsailFetchError("boto3 is missing")

    monkeypatch.setattr("apps.aws.admin.list_lightsail_regions", _boom)

    url = reverse("admin:aws_awscredentials_actions", args=["load_instances"])
    response = admin_client.post(url, follow=True)

    assert response.status_code == 200
    messages = [str(message) for message in response.context["messages"]]
    assert any("boto3 is missing" in message for message in messages)
