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


def test_credentials_tool_action_rejects_get(admin_client):
    """State-changing tool action should reject GET requests."""

    url = reverse("admin:aws_awscredentials_actions", args=["load_instances"])
    response = admin_client.get(url)

    assert response.status_code == 403


def test_instance_tool_action_rejects_get(admin_client):
    """Instance load tool action should reject GET requests."""

    url = reverse("admin:aws_lightsailinstance_actions", args=["load_instances"])
    response = admin_client.get(url)

    assert response.status_code == 403


