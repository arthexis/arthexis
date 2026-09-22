"""Explicit OCPP operation delivery without orchestration."""

from typing import Protocol

from asgiref.sync import sync_to_async
from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from django.utils import timezone

from apps.ocpp.domain.operations import complete_operation, create_operation
from apps.ocpp.models import Charger, ChargerConnection, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.errors import (
    ConnectionClosed,
    OutboundCallError,
    OutboundCallTimeout,
)
from apps.ocpp.protocol.v16.outbound import validate_outbound
from apps.ocpp.protocol.v201.outbound import validate_outbound as validate_v201_outbound
from apps.ocpp.services.presence import presence_cutoff


class Sender(Protocol):
    version: ProtocolVersion

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]: ...


class ActiveConnections:
    """In-process map of explicit live emitters, keyed by charger identity."""

    def __init__(self) -> None:
        self._senders: dict[int, Sender] = {}

    def register(self, charger: Charger, sender: Sender) -> None:
        self._senders[charger.pk] = sender

    def unregister(self, charger: Charger) -> None:
        self._senders.pop(charger.pk, None)

    def get(self, charger: Charger) -> Sender | None:
        return self._senders.get(charger.pk)


active_connections = ActiveConnections()


class ExplicitDeliveryUnavailable(RuntimeError):
    """An explicit operation cannot reach the owning WebSocket consumer."""


class ProtocolVersionMismatch(ExplicitDeliveryUnavailable):
    """The configured operation version differs from the live connection."""


async def register_connection(
    *,
    charger: Charger,
    sender: Sender,
    channel_name: str,
    version: ProtocolVersion,
) -> None:
    """Persist one authenticated consumer connection and retain its local sender."""
    active_connections.register(charger, sender)
    await sync_to_async(_record_connection)(charger, channel_name, version)


async def unregister_connection(*, charger: Charger, channel_name: str) -> None:
    """Clear a consumer presence record only when it still owns the channel."""
    active_connections.unregister(charger)
    await sync_to_async(_clear_connection)(charger, channel_name)


def _record_connection(
    charger: Charger, channel_name: str, version: ProtocolVersion
) -> None:
    now = timezone.now()
    Charger.objects.filter(pk=charger.pk).update(connected_at=now)
    ChargerConnection.objects.update_or_create(
        charger=charger,
        defaults={
            "channel_name": channel_name,
            "protocol": version,
            "last_seen_at": now,
        },
    )


def _clear_connection(charger: Charger, channel_name: str) -> None:
    deleted, _ = ChargerConnection.objects.filter(
        charger=charger, channel_name=channel_name
    ).delete()
    if deleted:
        Charger.objects.filter(pk=charger.pk).update(connected_at=None)


async def request_explicit_operation(
    *,
    charger: Charger,
    version: ProtocolVersion,
    action: str,
    payload: dict[str, object],
    timeout: float = 30,
) -> ProtocolOperation:
    """Queue one validated operator-requested operation for its live consumer."""
    request_payload = _validate_outbound(version, action, payload)
    connection = await sync_to_async(_load_connection)(charger)
    if connection is None:
        raise ExplicitDeliveryUnavailable("Charger is not connected.")
    if connection.protocol != version:
        raise ProtocolVersionMismatch(
            "Charger configuration does not match its live OCPP connection."
        )
    channel_layer = get_channel_layer()
    if channel_layer is None or not _is_shared_channel_layer(channel_layer):
        raise ExplicitDeliveryUnavailable(
            "A shared channel layer is required for explicit charger delivery."
        )
    operation = await sync_to_async(create_operation)(
        charger=charger,
        version=version,
        direction=Direction.CSMS_TO_CHARGE_POINT,
        action=action,
        request_payload=request_payload,
    )
    try:
        await channel_layer.send(
            connection.channel_name,
            {
                "type": "ocpp.emit",
                "operation_id": operation.pk,
                "timeout": timeout,
            },
        )
    except (ChannelFull, OSError, TimeoutError) as error:
        await sync_to_async(operation.delete)()
        raise ExplicitDeliveryUnavailable(
            "Could not reach the live charger consumer."
        ) from error
    return operation


async def deliver_queued_operation(
    *,
    charger: Charger,
    sender: Sender,
    version: ProtocolVersion,
    operation_id: int,
    timeout: float,
) -> None:
    """Deliver one command-queued operation from its owning consumer."""
    operation = await sync_to_async(_load_operation)(operation_id, charger)
    if operation is None:
        return
    if operation.version != version:
        await _finish_error(
            operation,
            "ProtocolError",
            "Queued operation version does not match this connection.",
        )
        return
    await _send_operation(operation, sender, timeout)


def _load_connection(charger: Charger) -> ChargerConnection | None:
    return ChargerConnection.objects.filter(
        charger=charger,
        last_seen_at__gte=presence_cutoff(),
    ).first()


def _load_operation(operation_id: int, charger: Charger) -> ProtocolOperation | None:
    return ProtocolOperation.objects.filter(pk=operation_id, charger=charger).first()


def _is_shared_channel_layer(channel_layer: object) -> bool:
    return channel_layer.__class__.__module__ != "channels.layers"


def _validate_outbound(
    version: ProtocolVersion, action: str, payload: dict[str, object]
) -> dict[str, object]:
    if version == ProtocolVersion.OCPP_16:
        return validate_outbound(action, payload)
    return validate_v201_outbound(action, payload)


async def emit_v16_operation(
    *, charger: Charger, action: str, payload: dict[str, object], timeout: float = 30
) -> ProtocolOperation:
    """Emit one explicit OCPP 1.6 request to an already-connected charger."""
    return await _emit_operation(
        charger=charger,
        action=action,
        payload=_validate_outbound(ProtocolVersion.OCPP_16, action, payload),
        timeout=timeout,
        version=ProtocolVersion.OCPP_16,
    )


async def emit_v201_operation(
    *, charger: Charger, action: str, payload: dict[str, object], timeout: float = 30
) -> ProtocolOperation:
    """Emit one explicit OCPP 2.0.1 request to an already-connected charger."""
    return await _emit_operation(
        charger=charger,
        action=action,
        payload=_validate_outbound(ProtocolVersion.OCPP_201, action, payload),
        timeout=timeout,
        version=ProtocolVersion.OCPP_201,
    )


async def _emit_operation(
    *,
    charger: Charger,
    action: str,
    payload: dict[str, object],
    timeout: float,
    version: ProtocolVersion,
) -> ProtocolOperation:
    operation = await sync_to_async(create_operation)(
        charger=charger,
        version=version,
        direction=Direction.CSMS_TO_CHARGE_POINT,
        action=action,
        request_payload=payload,
    )
    sender = active_connections.get(charger)
    if sender is None:
        return await _finish_status(
            operation,
            ProtocolOperation.Status.DISCONNECTED,
            "Charger is not connected.",
        )
    return await _send_operation(operation, sender, timeout)


async def _send_operation(
    operation: ProtocolOperation, sender: Sender, timeout: float
) -> ProtocolOperation:
    """Send a persisted operation through one already-owned consumer."""
    try:
        response = await sender.send(
            action=operation.action,
            payload=operation.request_payload,
            timeout=timeout,
            unique_id=str(operation.unique_id),
        )
    except OutboundCallError as error:
        return await _finish_error(operation, error.code, error.description)
    except OutboundCallTimeout as error:
        return await _finish_status(
            operation, ProtocolOperation.Status.TIMED_OUT, str(error)
        )
    except ConnectionClosed as error:
        return await _finish_status(
            operation, ProtocolOperation.Status.DISCONNECTED, str(error)
        )
    return await sync_to_async(complete_operation)(operation, response_payload=response)


async def _finish_error(
    operation: ProtocolOperation, code: str, description: str
) -> ProtocolOperation:
    return await sync_to_async(complete_operation)(
        operation,
        error_code=code,
        error_description=description,
    )


async def _finish_status(
    operation: ProtocolOperation, status: str, description: str
) -> ProtocolOperation:
    operation.status = status
    operation.error_description = description
    operation.completed_at = timezone.now()
    await sync_to_async(operation.save)(
        update_fields=("status", "error_description", "completed_at")
    )
    return operation
