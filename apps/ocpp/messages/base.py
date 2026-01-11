from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class MessageValidationError(ValueError):
    """Raised when an OCPP message payload fails validation."""


class MessageDecodeError(ValueError):
    """Raised when an OCPP call message cannot be decoded."""


@dataclass
class OcppMessageModel:
    payload: dict[str, object]
    ocpp_version: str | None = None
    message_id: str | None = None

    action: ClassVar[str]
    required_fields: ClassVar[dict[str, object]] = {}

    def __post_init__(self) -> None:
        self._validate_payload(self.payload)

    @classmethod
    def from_payload(
        cls,
        payload: object | None,
        *,
        ocpp_version: str | None = None,
        message_id: str | None = None,
    ) -> "OcppMessageModel":
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise MessageValidationError("payload must be a JSON object")
        return cls(payload=payload, ocpp_version=ocpp_version, message_id=message_id)

    def _validate_payload(self, payload: dict[str, object]) -> None:
        for field, field_types in self.required_fields.items():
            if field not in payload:
                raise MessageValidationError(f"missing required field '{field}'")
            value = payload[field]
            allowed_types = field_types
            if not isinstance(value, allowed_types):
                raise MessageValidationError(
                    f"field '{field}' must be {allowed_types}, got {type(value)}"
                )

    def to_payload(self) -> dict[str, object]:
        return _normalize_payload(self.payload)

    def get(self, key: str, default: object | None = None) -> object | None:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> object:
        return self.payload[key]


@dataclass
class OcppRequest(OcppMessageModel):
    pass


@dataclass
class OcppResponse(OcppMessageModel):
    pass


def _normalize_payload(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _normalize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    return value


def build_request_model(
    name: str,
    *,
    action: str,
    required_fields: dict[str, object] | None = None,
) -> type[OcppRequest]:
    return type(
        name,
        (OcppRequest,),
        {
            "action": action,
            "required_fields": required_fields or {},
        },
    )


def build_response_model(
    name: str,
    *,
    action: str,
    required_fields: dict[str, object] | None = None,
) -> type[OcppResponse]:
    return type(
        name,
        (OcppResponse,),
        {
            "action": action,
            "required_fields": required_fields or {},
        },
    )
