from __future__ import annotations

import time
from typing import Any, Optional

from .models import AWSCredentials, LightsailDatabase, LightsailInstance


try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError:
    boto3 = None
    BotoCoreError = ClientError = Exception


def _require_boto3():
    if boto3 is None:
        raise ImportError(
            "boto3 is required for AWS Lightsail operations. Install the optional AWS dependencies."
        )
    return boto3

class LightsailFetchError(Exception):
    """Raised when Lightsail resources cannot be fetched."""


class LightsailPaginationError(LightsailFetchError):
    """Raised when paginated Lightsail listing calls fail."""


def _lightsail_client(
    region: str,
    credentials: Optional[AWSCredentials] = None,
    *,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
):
    module = _require_boto3()
    session_kwargs: dict[str, Any] = {"region_name": region}
    if credentials is not None:
        session_kwargs.update(
            {
                "aws_access_key_id": credentials.access_key_id,
                "aws_secret_access_key": credentials.secret_access_key,
            }
        )
    elif access_key_id and secret_access_key:
        session_kwargs.update(
            {
                "aws_access_key_id": access_key_id,
                "aws_secret_access_key": secret_access_key,
            }
        )
    session = module.session.Session(**session_kwargs)
    return session.client("lightsail")


def list_lightsail_regions() -> list[str]:
    """Return available Lightsail regions from boto3 metadata."""

    try:
        module = _require_boto3()
    except ImportError as exc:
        raise LightsailFetchError(str(exc)) from exc
    session = module.session.Session()
    regions: list[str] = session.get_available_regions("lightsail") or []
    return sorted({code for code in regions})



def create_lightsail_instance(
    *,
    name: str,
    region: str,
    blueprint_id: str,
    bundle_id: str,
    credentials: Optional[AWSCredentials] = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    key_pair_name: str | None = None,
    availability_zone: str | None = None,
    wait_timeout_seconds: int = 90,
) -> dict[str, Any]:
    """Create a Lightsail instance and return fetched instance details when available."""

    client = _lightsail_client(
        region,
        credentials,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    payload: dict[str, Any] = {
        "instanceNames": [name],
        "availabilityZone": availability_zone or f"{region}a",
        "blueprintId": blueprint_id,
        "bundleId": bundle_id,
    }
    if key_pair_name:
        payload["keyPairName"] = key_pair_name

    try:
        client.create_instances(**payload)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - runtime safety
        raise LightsailFetchError(str(exc)) from exc

    deadline = time.time() + max(wait_timeout_seconds, 0)
    while time.time() <= deadline:
        try:
            details = fetch_lightsail_instance(
                name=name,
                region=region,
                credentials=credentials,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            )
        except LightsailFetchError as exc:
            original_exc = exc.__cause__
            if not (
                original_exc
                and isinstance(original_exc, ClientError)
                and original_exc.response.get("Error", {}).get("Code") == "NotFoundException"
            ):
                raise
            details = {}
        if details:
            return details
        time.sleep(3)

    return {}


def fetch_lightsail_instance(
    *,
    name: str,
    region: str,
    credentials: Optional[AWSCredentials] = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> dict[str, Any]:
    client = _lightsail_client(
        region,
        credentials,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    try:
        response = client.get_instance(instanceName=name)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - runtime safety
        raise LightsailFetchError(str(exc)) from exc
    return response.get("instance", {})


def delete_lightsail_instance(
    *,
    name: str,
    region: str,
    credentials: Optional[AWSCredentials] = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> None:
    """Delete a Lightsail instance."""

    client = _lightsail_client(
        region,
        credentials,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    try:
        client.delete_instance(instanceName=name, forceDeleteAddOns=True)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - runtime safety
        raise LightsailFetchError(str(exc)) from exc


def parse_instance_details(data: dict[str, Any]) -> dict[str, Any]:
    location = data.get("location") or {}
    state = data.get("state") or {}
    return {
        "availability_zone": location.get("availabilityZone", ""),
        "state": state.get("name", ""),
        "blueprint_id": data.get("blueprintId", ""),
        "bundle_id": data.get("bundleId", ""),
        "public_ip": data.get("publicIpAddress") or None,
        "private_ip": data.get("privateIpAddress") or None,
        "arn": data.get("arn", ""),
        "support_code": data.get("supportCode", ""),
        "created_at": data.get("createdAt"),
        "username": data.get("username", ""),
        "resource_type": data.get("resourceType", ""),
        "raw_details": LightsailInstance.serialize_details(data),
    }


def fetch_lightsail_database(
    *,
    name: str,
    region: str,
    credentials: Optional[AWSCredentials] = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> dict[str, Any]:
    client = _lightsail_client(
        region,
        credentials,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    try:
        response = client.get_relational_database(relationalDatabaseName=name)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - runtime safety
        raise LightsailFetchError(str(exc)) from exc
    return response.get("relationalDatabase", {})


def list_lightsail_instances(
    *,
    region: str,
    credentials: Optional[AWSCredentials] = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return all Lightsail instances for one region."""

    client = _lightsail_client(
        region,
        credentials,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    instances: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        try:
            if page_token:
                response = client.get_instances(pageToken=page_token)
            else:
                response = client.get_instances()
        except (BotoCoreError, ClientError) as exc:  # pragma: no cover - runtime safety
            raise LightsailPaginationError(str(exc)) from exc
        instances.extend(response.get("instances", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return instances


def consolidate_lightsail_instances(
    *,
    region: str,
    details: list[dict[str, Any]],
    credentials: AWSCredentials | None = None,
) -> tuple[int, int, list[tuple[LightsailInstance, bool]]]:
    """Create or update LightsailInstance rows from API payloads."""

    created_count = 0
    updated_count = 0
    processed_instances: list[tuple[LightsailInstance, bool]] = []
    for item in details:
        name = item.get("name")
        if not name:
            continue
        defaults = parse_instance_details(item)
        defaults.update({"region": region, "credentials": credentials})
        instance, created = LightsailInstance.objects.update_or_create(
            name=name,
            region=region,
            defaults=defaults,
        )
        processed_instances.append((instance, created))
        if created:
            created_count += 1
        else:
            updated_count += 1
    return created_count, updated_count, processed_instances


def parse_database_details(data: dict[str, Any]) -> dict[str, Any]:
    endpoint = data.get("masterEndpoint") or {}
    return {
        "availability_zone": data.get("availabilityZone", ""),
        "secondary_availability_zone": data.get("secondaryAvailabilityZone", ""),
        "state": data.get("state", ""),
        "engine": data.get("engine", ""),
        "engine_version": data.get("engineVersion", ""),
        "master_username": data.get("masterUsername", ""),
        "backup_retention_enabled": bool(data.get("backupRetentionEnabled", False)),
        "publicly_accessible": bool(data.get("publiclyAccessible", False)),
        "arn": data.get("arn", ""),
        "endpoint_address": endpoint.get("address", ""),
        "endpoint_port": endpoint.get("port"),
        "created_at": data.get("createdAt"),
        "raw_details": LightsailDatabase.serialize_details(data),
    }
