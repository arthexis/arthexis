"""Typed payload contracts shared across OCPP boundary modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NotRequired, Protocol, TypedDict, TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]


class PendingCallMetadata(TypedDict):
    """Common metadata retained while an outbound OCPP call is pending."""

    action: str
    charger_id: NotRequired[str]
    connector_id: NotRequired[int | str | None]
    log_key: NotRequired[str]
    auto_start_attempt_id: NotRequired[str]
    timeout_notice_sent: NotRequired[bool]


class CertificateHashData(TypedDict):
    hashAlgorithm: str
    issuerKeyHash: str
    issuerNameHash: str
    serialNumber: str


class CertificateStatusInfo(TypedDict):
    reasonCode: str
    additionalInfo: str


class CertificateStatusResponsePayload(TypedDict):
    status: str
    statusInfo: NotRequired[CertificateStatusInfo]


class OCSPResultPayload(TypedDict):
    status: str
    responderUrl: str
    producedAt: str
    thisUpdate: str
    nextUpdate: str
    errors: list[str]


HandlerPayload: TypeAlias = JSONObject
HandlerResponse: TypeAlias = JSONObject
Handler: TypeAlias = Callable[
    [HandlerPayload, str, str | None, str | None],
    Awaitable[HandlerResponse],
]


class SupportsHandle(Protocol):
    async def handle(
        self,
        payload: HandlerPayload,
        msg_id: str,
        raw: str | None,
        text_data: str | None,
    ) -> HandlerResponse: ...
