"""Regression tests for AWS Lightsail synchronization services."""

from __future__ import annotations

from django.db import IntegrityError

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import sync_lightsail_instances


def test_sync_lightsail_instances_creates_and_updates(monkeypatch, db) -> None:
    """Synchronization should create missing records and update existing records."""

    credential = AWSCredentials.objects.create(
        name="Primary",
        access_key_id="AKIASYNC",
        secret_access_key="secret",
    )
    LightsailInstance.objects.create(
        name="inst-a",
        region="us-east-1",
        state="stopped",
        credentials=credential,
    )

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


def test_sync_lightsail_instances_skips_cross_account_conflicts(monkeypatch, db) -> None:
    """Synchronization should not overwrite rows that belong to another credential."""

    first = AWSCredentials.objects.create(
        name="Account A",
        access_key_id="AKIAACC1",
        secret_access_key="secret-a",
    )
    second = AWSCredentials.objects.create(
        name="Account B",
        access_key_id="AKIAACC2",
        secret_access_key="secret-b",
    )

    LightsailInstance.objects.create(
        name="shared-name",
        region="us-east-1",
        state="running",
        credentials=first,
    )

    payload = [
        {
            "name": "shared-name",
            "state": {"name": "stopped"},
            "location": {"availabilityZone": "us-east-1a", "regionName": "us-east-1"},
        }
    ]

    monkeypatch.setattr("apps.aws.services.fetch_lightsail_instances", lambda **kwargs: payload)

    result = sync_lightsail_instances(credentials=second, regions=["us-east-1"])

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["conflicts"] == 1
    instance = LightsailInstance.objects.get(name="shared-name", region="us-east-1")
    assert instance.credentials == first
    assert instance.state == "running"


def test_sync_lightsail_instances_counts_integrity_error_as_conflict(monkeypatch, db) -> None:
    """Synchronization should continue when an upsert races on unique keys."""

    credential = AWSCredentials.objects.create(
        name="Race",
        access_key_id="AKIARACE",
        secret_access_key="secret",
    )

    payload = [
        {
            "name": "race-instance",
            "state": {"name": "running"},
            "location": {"availabilityZone": "us-east-1a", "regionName": "us-east-1"},
        }
    ]

    monkeypatch.setattr("apps.aws.services.fetch_lightsail_instances", lambda **kwargs: payload)


    def raise_integrity_error_once(*args, **kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(
        LightsailInstance.objects,
        "update_or_create",
        raise_integrity_error_once,
    )

    result = sync_lightsail_instances(credentials=credential, regions=["us-east-1"])

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["conflicts"] == 1
    assert not LightsailInstance.objects.filter(name="race-instance", region="us-east-1").exists()
