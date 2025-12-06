from __future__ import annotations

from typing import Any, Optional

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

from .models import AWSCredentials, LightsailDatabase, LightsailInstance


class LightsailFetchError(Exception):
    """Raised when Lightsail resources cannot be fetched."""


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
