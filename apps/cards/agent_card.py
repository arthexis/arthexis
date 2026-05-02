from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from django.conf import settings

AGENT_CARD_PREFIX = "AC1"
APPLICATION_SECTORS = range(1, 16)
IDENTITY_SECTORS = range(2, 6)
EXTENSION_SECTORS = range(6, 16)
SLOT_PAYLOAD_BYTES = 48
PRINTABLE_ASCII_MIN = 32
PRINTABLE_ASCII_MAX = 126
FRESH_READER_EVENT_SECONDS = 300
FUTURE_READER_EVENT_SKEW_SECONDS = 30
READER_PROOF_SECRET_SETTING = "AGENT_CARD_READER_PROOF_SECRET"

TRUST_TIERS = {
    "unknown",
    "local_authenticated",
    "trusted_operator_console",
    "trusted_gway",
    "provisioner",
}
ACTIVATING_TRUST_TIERS = TRUST_TIERS - {"unknown"}
SCRIPTLIKE_RE = re.compile(
    r"(\b(?:powershell|python|bash|cmd|sh)\b|[;&`$<>]|\b(?:select|insert|update|delete)\b)",
    re.IGNORECASE,
)
CREDENTIAL_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|ssh-rsa|BEGIN [A-Z ]*PRIVATE KEY)",
    re.IGNORECASE,
)
UNRESTRICTED_URL_RE = re.compile(r"https?://", re.IGNORECASE)
EXTENSION_OVERFLOW_NOTE = "Additional skill sigils were omitted because the card has ten extension slots."


class AgentCardError(ValueError):
    """Raised when Agent Card v1 data is malformed or unsafe."""


@dataclass(frozen=True)
class AgentCardRecord:
    sector: int
    slot_code: str
    fields: dict[str, str]
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentCardManifest:
    manifest: AgentCardRecord
    identity_slots: list[AgentCardRecord] = field(default_factory=list)
    capability_slots: list[AgentCardRecord] = field(default_factory=list)
    file_slots: list[AgentCardRecord] = field(default_factory=list)

    @property
    def records(self) -> list[AgentCardRecord]:
        return [
            self.manifest,
            *self.identity_slots,
            *self.capability_slots,
            *self.file_slots,
        ]

    @property
    def fingerprint(self) -> str:
        source = "\n".join(record.raw for record in self.records)
        return hashlib.sha256(source.encode("ascii")).hexdigest()

    def active_identity_tokens(self) -> set[tuple[str, str, str]]:
        tokens: set[tuple[str, str, str]] = set()
        for record in self.identity_slots:
            if record.fields.get("VOID") == "1":
                continue
            tokens.add(
                (
                    record.fields.get("NS", ""),
                    record.fields.get("ID", ""),
                    record.fields.get("H", ""),
                )
            )
        return tokens

    def capability_sigils(self) -> list[str]:
        return [
            record.fields["SIG"]
            for record in self.capability_slots
            if record.fields.get("SIG")
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "manifest": self.manifest.to_dict(),
            "identity_slots": [record.to_dict() for record in self.identity_slots],
            "capability_slots": [record.to_dict() for record in self.capability_slots],
            "file_slots": [record.to_dict() for record in self.file_slots],
        }


@dataclass(frozen=True)
class MatchScore:
    candidate_id: str
    matching_seeds: int
    available_seeds: int
    score: float
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReaderTrustResult:
    trusted: bool
    trust_tier: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActivationPlan:
    status: str
    reason: str
    manifest_fingerprint: str
    trust_tier: str
    skill_bundle_id: str = ""
    interface_spec_id: str = ""
    capability_sigils: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentCardBuildResult:
    sector_records: dict[int, str]
    padded_sector_records: dict[int, str]
    manifest: AgentCardManifest
    compatibility_notes: list[str] = field(default_factory=list)
    omitted_skill_sigils: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return self.manifest.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "sector_records": self.sector_records,
            "padded_sector_records": self.padded_sector_records,
            "manifest": self.manifest.to_dict(),
            "compatibility_notes": self.compatibility_notes,
            "omitted_skill_sigils": self.omitted_skill_sigils,
        }


def _sector_for_slot(slot_code: str) -> int | None:
    if slot_code == "M":
        return 1
    if slot_code.startswith("I") and slot_code[1:].isdigit():
        value = int(slot_code[1:])
        if 1 <= value <= 4:
            return value + 1
    if len(slot_code) == 3 and slot_code[0] in {"K", "F"} and slot_code[1:].isdigit():
        value = int(slot_code[1:])
        if 1 <= value <= 10:
            return value + 5
    return None


def _compact_digest(value: object, *, length: int = 12) -> str:
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length].upper()


def _pad_record(record: str) -> str:
    encoded = record.encode("ascii", errors="strict")
    if len(encoded) > SLOT_PAYLOAD_BYTES:
        raise AgentCardError(f"Agent Card record exceeds {SLOT_PAYLOAD_BYTES} bytes.")
    return record.ljust(SLOT_PAYLOAD_BYTES)


def _identity_record(slot_index: int, source_value: object) -> str:
    if not source_value:
        return f"AC1|I{slot_index}|VOID=1"
    source_text = str(source_value)
    return (
        f"AC1|I{slot_index}|NS=SOUL|"
        f"ID={_compact_digest(f'id:{source_text}', length=8)}|"
        f"H={_compact_digest(f'hash:{source_text}', length=12)}"
    )


def _empty_extension_record(slot_index: int, source_value: object = "") -> str:
    digest_source = source_value or f"empty-extension-{slot_index}"
    return f"AC1|F{slot_index:02d}|T=EMPTY|H={_compact_digest(digest_source, length=6)}"


def _skill_sigil_for_slug(skill_slug: str) -> str:
    return f"[AGENT.SKILL:{skill_slug}]"


def _coerce_sector_payloads(
    sector_payloads: Mapping[int | str, str | bytes] | Iterable[str | bytes],
) -> dict[int, str | bytes]:
    if isinstance(sector_payloads, Mapping):
        coerced: dict[int, str | bytes] = {}
        for key, value in sector_payloads.items():
            try:
                sector = int(key)
            except (TypeError, ValueError) as error:
                raise AgentCardError(f"Invalid sector key: {key!r}") from error
            coerced[sector] = value
        return coerced
    if isinstance(sector_payloads, str | bytes):
        raise AgentCardError("Agent Card v1 sectors must be a mapping or iterable of payloads.")
    try:
        values = list(sector_payloads)
    except TypeError as error:
        raise AgentCardError(
            "Agent Card v1 sectors must be a mapping or iterable of payloads."
        ) from error
    if len(values) != len(APPLICATION_SECTORS):
        raise AgentCardError("Agent Card v1 requires exactly 15 application sectors.")
    return {sector: values[index] for index, sector in enumerate(APPLICATION_SECTORS)}


def build_agent_card_sector_payloads(
    *,
    identity_sources: Mapping[str, object],
    skill_slugs: Iterable[str],
) -> AgentCardBuildResult:
    """Build a complete Agent Card v1 sector map and validate it with the parser."""

    ordered_identity_sources = [
        identity_sources.get("intent"),
        identity_sources.get("bundle"),
        identity_sources.get("interface"),
        identity_sources.get("card"),
    ]
    sector_records: dict[int, str] = {
        1: "AC1|M|S=4|X=10|ALG=SHA8|POL=RDRSIG",
    }
    for index, source_value in enumerate(ordered_identity_sources, start=1):
        sector_records[index + 1] = _identity_record(index, source_value)

    compatibility_notes: list[str] = []
    omitted_skill_sigils: list[str] = []
    extension_index = 1
    for raw_slug in skill_slugs:
        slug = str(raw_slug or "").strip()
        if not slug:
            continue
        sigil = _skill_sigil_for_slug(slug)
        if extension_index > len(EXTENSION_SECTORS):
            if EXTENSION_OVERFLOW_NOTE not in compatibility_notes:
                compatibility_notes.append(EXTENSION_OVERFLOW_NOTE)
            omitted_skill_sigils.append(sigil)
            continue
        record = (
            f"AC1|K{extension_index:02d}|SIG={sigil}|"
            f"H={_compact_digest(f'skill:{slug}', length=6)}"
        )
        try:
            _parse_record(extension_index + 5, record)
        except AgentCardError:
            compatibility_notes.append(
                f"Skill sigil for '{slug}' does not fit Agent Card v1 sector limits."
            )
            omitted_skill_sigils.append(sigil)
            continue
        sector_records[extension_index + 5] = record
        extension_index += 1

    while extension_index <= len(EXTENSION_SECTORS):
        sector_records[extension_index + 5] = _empty_extension_record(
            extension_index,
            identity_sources.get("card"),
        )
        extension_index += 1

    manifest = parse_agent_card(sector_records)
    padded_records = {sector: _pad_record(record) for sector, record in sector_records.items()}
    return AgentCardBuildResult(
        sector_records=sector_records,
        padded_sector_records=padded_records,
        manifest=manifest,
        compatibility_notes=compatibility_notes,
        omitted_skill_sigils=omitted_skill_sigils,
    )


def _trim_and_validate_payload(sector: int, payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        if len(payload) > SLOT_PAYLOAD_BYTES:
            raise AgentCardError(f"Sector {sector} exceeds 48 bytes.")
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as error:
            raise AgentCardError(f"Sector {sector} contains non-ASCII data.") from error
    else:
        try:
            encoded = str(payload).encode("ascii", errors="strict")
        except UnicodeEncodeError as error:
            raise AgentCardError(f"Sector {sector} contains non-ASCII data.") from error
        if len(encoded) > SLOT_PAYLOAD_BYTES:
            raise AgentCardError(f"Sector {sector} exceeds 48 bytes.")
        text = str(payload)

    for char in text:
        ordinal = ord(char)
        if ordinal < PRINTABLE_ASCII_MIN or ordinal > PRINTABLE_ASCII_MAX:
            raise AgentCardError(f"Sector {sector} contains non-printable data.")
    record = text.rstrip(" ")
    if not record:
        raise AgentCardError(f"Sector {sector} is blank.")
    return record


def _parse_record(sector: int, payload: str | bytes) -> AgentCardRecord:
    raw = _trim_and_validate_payload(sector, payload)
    parts = raw.split("|")
    if len(parts) < 2 or parts[0] != AGENT_CARD_PREFIX:
        raise AgentCardError(f"Sector {sector} is not an Agent Card v1 record.")
    slot_code = parts[1]
    expected_sector = _sector_for_slot(slot_code)
    if expected_sector != sector:
        raise AgentCardError(f"Sector {sector} contains wrong slot code: {slot_code}.")

    fields: dict[str, str] = {}
    for fragment in parts[2:]:
        if "=" not in fragment:
            raise AgentCardError(f"Sector {sector} contains invalid field: {fragment}.")
        key, value = fragment.split("=", 1)
        if not key or not re.fullmatch(r"[A-Z0-9_]+", key):
            raise AgentCardError(f"Sector {sector} contains invalid field key: {key}.")
        if CREDENTIAL_RE.search(value):
            raise AgentCardError(f"Sector {sector} contains credential-like payload.")
        if SCRIPTLIKE_RE.search(value):
            raise AgentCardError(f"Sector {sector} contains script-like payload.")
        if UNRESTRICTED_URL_RE.search(value):
            raise AgentCardError(f"Sector {sector} contains unrestricted URL payload.")
        fields[key] = value
    return AgentCardRecord(sector=sector, slot_code=slot_code, fields=fields, raw=raw)


def parse_agent_card(
    sector_payloads: Mapping[int | str, str | bytes] | Iterable[str | bytes],
) -> AgentCardManifest:
    """Parse and validate the fixed Agent Card v1 application sectors."""

    payloads = _coerce_sector_payloads(sector_payloads)
    missing = [sector for sector in APPLICATION_SECTORS if sector not in payloads]
    if missing:
        raise AgentCardError(f"Missing application sectors: {missing}")
    extra = sorted(set(payloads) - set(APPLICATION_SECTORS))
    if extra:
        raise AgentCardError(f"Unexpected application sectors: {extra}")

    records = {sector: _parse_record(sector, payloads[sector]) for sector in APPLICATION_SECTORS}
    manifest = records[1]
    identity_slots = [records[sector] for sector in IDENTITY_SECTORS]
    capability_slots = [
        records[sector]
        for sector in EXTENSION_SECTORS
        if records[sector].slot_code.startswith("K")
    ]
    file_slots = [
        records[sector]
        for sector in EXTENSION_SECTORS
        if records[sector].slot_code.startswith("F")
    ]
    for record in identity_slots:
        if record.fields.get("VOID") == "1":
            continue
        if not {"NS", "ID", "H"}.issubset(record.fields):
            raise AgentCardError(f"Identity slot {record.slot_code} is incomplete.")
    return AgentCardManifest(
        manifest=manifest,
        identity_slots=identity_slots,
        capability_slots=capability_slots,
        file_slots=file_slots,
    )


def _candidate_tokens(candidate: Mapping[str, Any] | object) -> set[tuple[str, str, str]]:
    raw_tokens = None
    if isinstance(candidate, Mapping):
        raw_tokens = candidate.get("seeds") or candidate.get("seed_slots")
    else:
        raw_tokens = getattr(candidate, "seeds", None) or getattr(candidate, "seed_slots", None)
    tokens: set[tuple[str, str, str]] = set()
    for token in raw_tokens or []:
        if isinstance(token, Mapping):
            tokens.add((str(token.get("NS", "")), str(token.get("ID", "")), str(token.get("H", ""))))
        elif isinstance(token, (list, tuple)) and len(token) == 3:
            tokens.add((str(token[0]), str(token[1]), str(token[2])))
    return tokens


def _candidate_id(candidate: Mapping[str, Any] | object) -> str:
    if isinstance(candidate, Mapping):
        return str(candidate.get("id") or candidate.get("soul_id") or candidate.get("name") or "")
    return str(getattr(candidate, "id", None) or getattr(candidate, "soul_id", "") or getattr(candidate, "name", ""))


def _expected_reader_proof(
    *,
    trust_tier: str,
    reader_id: str,
    node_id: str,
    observed_at: str | datetime,
    manifest_fingerprint: str,
    secret: str | bytes | None = None,
) -> str:
    payload = {
        "manifest_fingerprint": str(manifest_fingerprint),
        "node_id": str(node_id),
        "observed_at": _reader_observed_at_value(observed_at),
        "reader_id": str(reader_id),
        "trust_tier": str(trust_tier),
    }
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_reader_proof_secret(secret), canonical_payload, hashlib.sha256).hexdigest()


def _reader_proof_secret(secret: str | bytes | None = None) -> bytes:
    raw_secret = secret
    if raw_secret is None:
        raw_secret = getattr(
            settings,
            READER_PROOF_SECRET_SETTING,
            getattr(settings, "SECRET_KEY", ""),
        )
    if isinstance(raw_secret, bytes):
        return raw_secret
    return str(raw_secret).encode("utf-8")


def _reader_observed_at_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "").strip()


def _confidence_for_count(count: int) -> str:
    if count <= 0:
        return "no_match"
    if count == 1:
        return "weak"
    if count == 2:
        return "plausible"
    if count == 3:
        return "strong"
    return "best"


def score_soul_identity(
    card: AgentCardManifest,
    registry_candidates: Iterable[Mapping[str, Any] | object],
) -> MatchScore:
    card_tokens = card.active_identity_tokens()
    best = MatchScore(
        candidate_id="",
        matching_seeds=0,
        available_seeds=len(card_tokens),
        score=0.0,
        confidence="no_match",
    )
    for candidate in registry_candidates:
        candidate_tokens = _candidate_tokens(candidate)
        matching = len(card_tokens & candidate_tokens)
        score = matching / max(len(card_tokens), 1)
        candidate_score = MatchScore(
            candidate_id=_candidate_id(candidate),
            matching_seeds=matching,
            available_seeds=len(card_tokens),
            score=round(score, 4),
            confidence=_confidence_for_count(matching),
        )
        if (candidate_score.matching_seeds, candidate_score.score) > (best.matching_seeds, best.score):
            best = candidate_score
    return best


def validate_reader_event(
    reader_event: Mapping[str, Any],
    *,
    manifest_fingerprint: str,
) -> ReaderTrustResult:
    trust_tier = str(reader_event.get("trust_tier") or "unknown").strip()
    if trust_tier not in TRUST_TIERS:
        return ReaderTrustResult(False, "unknown", "unknown reader trust tier")
    if trust_tier not in ACTIVATING_TRUST_TIERS:
        return ReaderTrustResult(False, trust_tier, "reader is not trusted for activation")
    observed_at = _reader_observed_at_value(reader_event.get("observed_at"))
    if not observed_at:
        return ReaderTrustResult(False, trust_tier, "reader event timestamp is required")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return ReaderTrustResult(False, trust_tier, "reader event timestamp is invalid")
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    observed = observed.astimezone(timezone.utc)
    if observed - now > timedelta(seconds=FUTURE_READER_EVENT_SKEW_SECONDS):
        return ReaderTrustResult(False, trust_tier, "reader event timestamp is in the future")
    age = now - observed
    if age > timedelta(seconds=FRESH_READER_EVENT_SECONDS):
        return ReaderTrustResult(False, trust_tier, "reader event is stale")
    reader_id = str(reader_event.get("reader_id") or "").strip()
    node_id = str(reader_event.get("node_id") or "").strip()
    if not reader_id:
        return ReaderTrustResult(False, trust_tier, "reader_id is required")
    if not node_id:
        return ReaderTrustResult(False, trust_tier, "node_id is required")
    proof = str(reader_event.get("proof") or "").strip()
    if not proof:
        return ReaderTrustResult(False, trust_tier, "reader proof is required")
    expected_proof = _expected_reader_proof(
        trust_tier=trust_tier,
        reader_id=reader_id,
        node_id=node_id,
        observed_at=observed_at,
        manifest_fingerprint=manifest_fingerprint,
    )
    if not hmac.compare_digest(proof, expected_proof):
        return ReaderTrustResult(False, trust_tier, "reader proof is invalid")
    return ReaderTrustResult(True, trust_tier)


def plan_agent_activation(
    card: AgentCardManifest,
    reader_event: Mapping[str, Any],
    *,
    skill_bundle_id: str | int | None = None,
    interface_spec_id: str | int | None = None,
) -> ActivationPlan:
    trust = validate_reader_event(reader_event, manifest_fingerprint=card.fingerprint)
    if not trust.trusted:
        return ActivationPlan(
            status="rejected",
            reason=trust.reason,
            manifest_fingerprint=card.fingerprint,
            trust_tier=trust.trust_tier,
        )
    if not skill_bundle_id:
        return ActivationPlan(
            status="rejected",
            reason="skill bundle is required",
            manifest_fingerprint=card.fingerprint,
            trust_tier=trust.trust_tier,
        )
    if not interface_spec_id:
        return ActivationPlan(
            status="rejected",
            reason="interface spec is required",
            manifest_fingerprint=card.fingerprint,
            trust_tier=trust.trust_tier,
        )
    return ActivationPlan(
        status="ready",
        reason="activation plan accepted",
        manifest_fingerprint=card.fingerprint,
        trust_tier=trust.trust_tier,
        skill_bundle_id=str(skill_bundle_id),
        interface_spec_id=str(interface_spec_id),
        capability_sigils=card.capability_sigils(),
    )
