"""Stable inbound OCPP replay identity primitives."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

from apps.ocpp.protocol.contracts import ProtocolVersion


class ReplayPolicy(str, Enum):
    """How an inbound action may identify retransmissions."""

    CALL_ID_AND_FINGERPRINT = "call_id_and_fingerprint"
    DOMAIN_IDENTITY = "domain_identity"
    NO_CROSS_CALL_DEDUP = "no_cross_call_dedup"


@dataclass(frozen=True)
class ReplayIdentity:
    """Canonical evidence used by the durable inbound replay ledger."""

    policy: ReplayPolicy
    fingerprint: str
    call_id: str
    domain_identity: str = ""

    def logical_key(self) -> tuple[str, ...]:
        """Return the policy-specific identity components excluding charger scope."""
        if self.policy is ReplayPolicy.DOMAIN_IDENTITY:
            if not self.domain_identity:
                raise ValueError("Domain replay identity is required.")
            return (self.policy.value, self.domain_identity, self.fingerprint)
        return (self.policy.value, self.call_id, self.fingerprint)


def canonical_payload(payload: Mapping[str, object]) -> str:
    """Serialize one JSON object deterministically for replay identity hashing."""
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def request_fingerprint(
    *,
    version: ProtocolVersion | str,
    action: str,
    payload: Mapping[str, object],
) -> str:
    """Hash the protocol/action namespace and canonical JSON request payload."""
    if not action:
        raise ValueError("OCPP action is required.")
    version_value = version.value if isinstance(version, ProtocolVersion) else str(version)
    if not version_value:
        raise ValueError("OCPP protocol version is required.")

    canonical = canonical_payload(payload)
    material = f"{version_value}\n{action}\n{canonical}".encode()
    return sha256(material).hexdigest()


def replay_identity(
    *,
    version: ProtocolVersion | str,
    action: str,
    call_id: str,
    payload: Mapping[str, object],
    policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
    domain_identity: str = "",
) -> ReplayIdentity:
    """Build one immutable replay identity from normalized request evidence."""
    if not call_id:
        raise ValueError("OCPP call ID is required.")
    identity = ReplayIdentity(
        policy=policy,
        fingerprint=request_fingerprint(
            version=version,
            action=action,
            payload=payload,
        ),
        call_id=call_id,
        domain_identity=domain_identity,
    )
    if policy is ReplayPolicy.DOMAIN_IDENTITY and not domain_identity:
        raise ValueError("Domain replay identity is required.")
    return identity


def identity_key(identity: ReplayIdentity) -> str:
    """Hash one policy-specific logical replay identity for SQL uniqueness."""
    material = "\n".join(identity.logical_key()).encode()
    return sha256(material).hexdigest()


_TRANSACTION_REPLAY_ACTIONS = frozenset(
    {"StartTransaction", "StopTransaction", "MeterValues"}
)


def replay_policy_for_action(action: str) -> ReplayPolicy:
    """Select the default replay policy for one inbound action."""
    if action == "TransactionEvent":
        return ReplayPolicy.DOMAIN_IDENTITY
    if action in _TRANSACTION_REPLAY_ACTIONS:
        return ReplayPolicy.CALL_ID_AND_FINGERPRINT
    return ReplayPolicy.NO_CROSS_CALL_DEDUP


def replay_context_for_action(
    action: str,
    payload: Mapping[str, object],
) -> tuple[ReplayPolicy, str]:
    """Return policy and optional domain identity for one inbound request."""
    policy = replay_policy_for_action(action)
    if policy is not ReplayPolicy.DOMAIN_IDENTITY:
        return policy, ""
    domain_identity = _transaction_event_identity(payload)
    if not domain_identity:
        return ReplayPolicy.CALL_ID_AND_FINGERPRINT, ""
    return policy, domain_identity


def _transaction_event_identity(payload: Mapping[str, object]) -> str:
    info = payload.get("transactionInfo")
    event_type = payload.get("eventType")
    seq_no = payload.get("seqNo")
    if not isinstance(info, Mapping):
        return ""
    transaction_id = info.get("transactionId")
    if (
        not isinstance(transaction_id, str)
        or not transaction_id
        or not isinstance(event_type, str)
        or not event_type
        or not isinstance(seq_no, int)
    ):
        return ""
    return f"{transaction_id}:{seq_no}:{event_type}"
