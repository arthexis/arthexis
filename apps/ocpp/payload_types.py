"""Typed payload contracts shared across OCPP boundary modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypedDict, TypeAlias
from typing_extensions import NotRequired


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]


class CertificateHashData(TypedDict, total=False):
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
