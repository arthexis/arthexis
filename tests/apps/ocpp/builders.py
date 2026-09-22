"""Small OCPP test builders that keep setup explicit without repetition."""

from datetime import datetime, timedelta

from django.utils import timezone

from apps.ocpp.models import (
    Charger,
    ChargerConnection,
    Connector,
    OcppTransaction,
    StationModel,
)


def station_model(
    *,
    model: str = "Model",
    protocol: str = "ocpp1.6",
    vendor: str = "ACME",
) -> StationModel:
    return StationModel.objects.create(
        vendor=vendor,
        model=model,
        preferred_protocol=protocol,
    )


def charger(
    identity: str,
    *,
    station: StationModel | None = None,
    active: bool = True,
    **values,
) -> Charger:
    return Charger.objects.create(
        identity=identity,
        station_model=station,
        active=active,
        **values,
    )


def connection(
    charger: Charger,
    *,
    protocol: str = "ocpp1.6",
    channel_name: str | None = None,
    heartbeat_interval_seconds: int | None = None,
    lease_seconds: int = 900,
) -> ChargerConnection:
    now = timezone.now()
    return ChargerConnection.objects.create(
        charger=charger,
        channel_name=channel_name or f"{charger.identity}.channel",
        protocol=protocol,
        last_seen_at=now,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )


def connector(
    charger: Charger,
    *,
    number: int = 1,
    status: str = "Available",
) -> Connector:
    return Connector.objects.create(
        charger=charger,
        number=number,
        status=status,
    )


def transaction(
    charger: Charger,
    remote_id: str,
    *,
    started_at: datetime,
    stopped_at: datetime | None = None,
    historical: bool = False,
    **values,
) -> OcppTransaction:
    values.setdefault("last_activity_at", stopped_at or started_at)
    values.setdefault(
        "recovery_state",
        (
            OcppTransaction.RecoveryState.COMPLETED
            if stopped_at is not None
            else OcppTransaction.RecoveryState.ACTIVE
        ),
    )
    return OcppTransaction.objects.create(
        charger=charger,
        remote_id=remote_id,
        started_at=started_at,
        stopped_at=stopped_at,
        historical=historical,
        **values,
    )
