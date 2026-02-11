"""Payload parsing and validation types for node registration."""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.http import JsonResponse

from apps.nodes.models import Node


@dataclass(frozen=True)
class PayloadValidationResult:
    """Structured validation result for registration payload checks."""

    is_valid: bool
    detail: str = ""
    status: int = 200

    def to_response(self):
        """Return a JSON response when invalid, else ``None``."""

        if self.is_valid:
            return None
        return JsonResponse({"detail": self.detail}, status=self.status)


@dataclass(frozen=True)
class NodeRegistrationPayload:
    """Typed DTO for incoming node registration request data."""

    hostname: str
    mac_address: str
    address: str
    network_hostname: str
    ipv4_candidates: list[str]
    ipv6_address: str
    port: int
    features: object
    public_key: str | None
    token: str | None
    signature: str | None
    installed_version: object | None
    installed_revision: object | None
    relation_value: Node.Relation | None
    trusted_requested: object
    role_name: str
    deactivate_user: bool
    base_site_domain: str


@dataclass(frozen=True)
class RegistrationRequestDTO:
    """Parsed registration request carrying both raw and typed data."""

    raw_data: dict | object
    payload: NodeRegistrationPayload


def _coerce_bool(value) -> bool:
    """Coerce string and scalar values into booleans."""

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_port(value) -> int:
    """Coerce port values to int with default registration port."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 8888


def _extract_request_data(request):
    """Parse JSON request body with POST-data fallback."""

    try:
        return json.loads(request.body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return request.POST


def _extract_features(data):
    """Extract features preserving multipart list semantics."""

    if hasattr(data, "getlist"):
        raw_features = data.getlist("features")
        if not raw_features:
            return None
        if len(raw_features) == 1:
            return raw_features[0]
        return raw_features
    return data.get("features")


def _extract_ipv4_candidates(data) -> list[str]:
    """Extract and sanitize IPv4 candidate values from payload data."""

    if hasattr(data, "getlist"):
        ipv4_values = data.getlist("ipv4_address")
        raw_ipv4 = ipv4_values if ipv4_values else data.get("ipv4_address")
    else:
        raw_ipv4 = data.get("ipv4_address")
    return Node.sanitize_ipv4_addresses(raw_ipv4)


def build_payload(data) -> NodeRegistrationPayload:
    """Build typed registration payload from raw form or JSON input."""

    raw_relation = data.get("current_relation")
    relation_present = (
        hasattr(data, "getlist") and "current_relation" in data
    ) or ("current_relation" in data)

    return NodeRegistrationPayload(
        hostname=(data.get("hostname") or "").strip(),
        mac_address=(data.get("mac_address") or "").strip(),
        address=(data.get("address") or "").strip(),
        network_hostname=(data.get("network_hostname") or "").strip(),
        ipv4_candidates=_extract_ipv4_candidates(data),
        ipv6_address=(data.get("ipv6_address") or "").strip(),
        port=_coerce_port(data.get("port", 8888)),
        features=_extract_features(data),
        public_key=data.get("public_key"),
        token=data.get("token"),
        signature=data.get("signature"),
        installed_version=data.get("installed_version"),
        installed_revision=data.get("installed_revision"),
        relation_value=Node.normalize_relation(raw_relation) if relation_present else None,
        trusted_requested=data.get("trusted"),
        role_name=str(data.get("role") or data.get("role_name") or "").strip(),
        deactivate_user=_coerce_bool(data.get("deactivate_user")),
        base_site_domain=str(data.get("base_site_domain") or "").strip(),
    )


def parse_registration_request(request) -> RegistrationRequestDTO:
    """Parse request and return structured DTO for registration handler."""

    raw_data = _extract_request_data(request)
    return RegistrationRequestDTO(raw_data=raw_data, payload=build_payload(raw_data))


def validate_payload(payload: NodeRegistrationPayload) -> PayloadValidationResult:
    """Validate registration payload required fields and addressing rules."""

    if not payload.hostname or not payload.mac_address:
        return PayloadValidationResult(
            is_valid=False, detail="hostname and mac_address required", status=400
        )
    if not any(
        [
            payload.address,
            payload.network_hostname,
            bool(payload.ipv4_candidates),
            payload.ipv6_address,
        ]
    ):
        return PayloadValidationResult(
            is_valid=False,
            detail=(
                "at least one of address, network_hostname, "
                "ipv4_address, or ipv6_address must be provided"
            ),
            status=400,
        )
    return PayloadValidationResult(is_valid=True)
