from __future__ import annotations

import json
from dataclasses import dataclass

from .base import MessageDecodeError, MessageValidationError, OcppRequest, OcppResponse
from . import ocpp16, ocpp201, ocpp21


OCPP_VERSION_16 = "ocpp1.6"
OCPP_VERSION_201 = "ocpp2.0.1"
OCPP_VERSION_21 = "ocpp2.1"


@dataclass
class DecodedCall:
    message_id: str
    action: str
    ocpp_version: str
    request: OcppRequest


REQUEST_REGISTRY: dict[tuple[int, str, str], type[OcppRequest]] = {}
RESPONSE_REGISTRY: dict[tuple[int, str, str], type[OcppResponse]] = {}


def _register_version(version: str, request_models, response_models) -> None:
    for action, model in request_models.items():
        REQUEST_REGISTRY[(2, action, version)] = model
    for action, model in response_models.items():
        RESPONSE_REGISTRY[(3, action, version)] = model


_register_version(OCPP_VERSION_16, ocpp16.REQUEST_MODELS, ocpp16.RESPONSE_MODELS)
_register_version(OCPP_VERSION_201, ocpp201.REQUEST_MODELS, ocpp201.RESPONSE_MODELS)
_register_version(OCPP_VERSION_21, ocpp21.REQUEST_MODELS, ocpp21.RESPONSE_MODELS)


def decode_call(raw_message: str | list[object], *, ocpp_version: str) -> DecodedCall:
    if isinstance(raw_message, str):
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise MessageDecodeError("invalid JSON") from exc
    else:
        message = raw_message
    if not isinstance(message, list) or len(message) < 3:
        raise MessageDecodeError("message must be an OCPP call array")
    message_type_id = message[0]
    if message_type_id != 2:
        raise MessageDecodeError("message is not a Call")
    message_id = str(message[1])
    action = str(message[2])
    payload = message[3] if len(message) > 3 else {}
    model_key = (2, action, ocpp_version)
    model_cls = REQUEST_REGISTRY.get(model_key)
    if model_cls is None:
        raise MessageDecodeError(
            f"unsupported action '{action}' for protocol '{ocpp_version}'"
        )
    try:
        request = model_cls.from_payload(
            payload, ocpp_version=ocpp_version, message_id=message_id
        )
    except MessageValidationError as exc:
        raise MessageDecodeError(str(exc)) from exc
    return DecodedCall(
        message_id=message_id,
        action=action,
        ocpp_version=ocpp_version,
        request=request,
    )


def encode_call(request: OcppRequest, *, message_id: str) -> list[object]:
    payload = request.to_payload()
    request.message_id = message_id
    return [2, message_id, request.action, payload]


def encode_call_result(response: OcppResponse) -> list[object]:
    if not response.message_id:
        raise MessageValidationError("response is missing message_id")
    payload = response.to_payload()
    return [3, response.message_id, payload]


def build_request(action: str, *, ocpp_version: str, payload: dict[str, object]) -> OcppRequest:
    model_cls = REQUEST_REGISTRY.get((2, action, ocpp_version))
    if model_cls is None:
        raise MessageDecodeError(
            f"unsupported action '{action}' for protocol '{ocpp_version}'"
        )
    return model_cls.from_payload(payload, ocpp_version=ocpp_version)


def build_response(action: str, *, ocpp_version: str, payload: dict[str, object]) -> OcppResponse:
    model_cls = RESPONSE_REGISTRY.get((3, action, ocpp_version))
    if model_cls is None:
        raise MessageDecodeError(
            f"unsupported action '{action}' for protocol '{ocpp_version}'"
        )
    return model_cls.from_payload(payload, ocpp_version=ocpp_version)
