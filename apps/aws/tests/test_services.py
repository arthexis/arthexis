"""Regression tests for AWS Lightsail synchronization services."""

from __future__ import annotations

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import sync_lightsail_instances



def test_sync_lightsail_instances_creates_and_updates(monkeypatch, db) -> None:
    """Synchronization should create missing records and update existing records."""

    credential = AWSCredentials.objects.create(
        name="Primary",
        access_key_id="AKIASYNC",
        secret_access_key="secret",
    )
    LightsailInstance.objects.create(name="inst-a", region="us-east-1", state="stopped")

    payload = [
        {
            "name": "inst-a",
            "state": {"name": "running"},
            "location": {"availabilityZone": "us-east-1a", "regionName": "us-east-1"},
        },
        {
            "name": "inst-b",
            "state": {"name": "pending"},
            "location": {"availabilityZone": "us-east-1b", "regionName": "us-east-1"},
        },
    ]

    monkeypatch.setattr("apps.aws.services.fetch_lightsail_instances", lambda **kwargs: payload)

    result = sync_lightsail_instances(credentials=credential, regions=["us-east-1"])

    assert result["created"] == 1
    assert result["updated"] == 1
    assert LightsailInstance.objects.filter(name="inst-a", state="running").exists()
    assert LightsailInstance.objects.filter(name="inst-b", credentials=credential).exists()
