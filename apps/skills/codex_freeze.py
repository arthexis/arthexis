from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.skills.models import CodexFeatureFreeze

DEFAULT_FREEZE_STATE_URL = ""
DEFAULT_TIMEOUT_SECONDS = 1.5
EMERGENCY_BYPASS_REASON_ENV = "ARTHEXIS_CODEX_FREEZE_BYPASS_REASON"
EMERGENCY_BYPASS_UNTIL_ENV = "ARTHEXIS_CODEX_FREEZE_BYPASS_UNTIL"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreezeDecision:
    decision: str
    reason: str = ""
    source: str = "default"
    state: dict[str, object] | None = None
    error: str = ""
    bypass_until: datetime | None = None

    def as_hook_payload(self) -> dict[str, object]:
        payload = {"decision": self.decision}
        if self.reason:
            payload["reason"] = self.reason
        return payload


def cache_path() -> Path:
    return Path(settings.BASE_DIR) / "work" / "codex" / "freeze-state.json"


def freeze_state_url() -> str:
    return os.environ.get("ARTHEXIS_CODEX_FREEZE_URL", DEFAULT_FREEZE_STATE_URL).strip()


def signing_key() -> str:
    return os.environ.get("ARTHEXIS_CODEX_FREEZE_SIGNING_KEY", "").strip()


def freeze_status() -> dict[str, object]:
    decision = check_freeze(refresh=False)
    return {
        "bypass_until": _isoformat(decision.bypass_until)
        if decision.bypass_until
        else None,
        "cache_path": str(cache_path()),
        "decision": decision.decision,
        "reason": decision.reason,
        "remote_url": freeze_state_url(),
        "source": decision.source,
        "state": decision.state,
    }


def local_freeze_state() -> dict[str, object]:
    try:
        freeze = CodexFeatureFreeze.current()
        latest = CodexFeatureFreeze.objects.order_by("-policy_version").first()
    except DatabaseError:
        freeze = None
        latest = None
    if freeze is None:
        version = latest.policy_version if latest else 0
        state = {
            "active": False,
            "reason": "",
            "starts_at": None,
            "ends_at": None,
            "policy_version": version,
        }
    else:
        state = {
            "active": True,
            "reason": freeze.reason,
            "starts_at": _isoformat(freeze.starts_at),
            "ends_at": _isoformat(freeze.ends_at),
            "policy_version": freeze.policy_version,
        }
    state["generated_at"] = _isoformat(timezone.now())
    state["max_age_seconds"] = 300
    return sign_state(state)


def sign_state(state: dict[str, object]) -> dict[str, object]:
    key = signing_key()
    payload = dict(state)
    payload.pop("signature", None)
    if key:
        payload["signature"] = _signature(payload, key)
    return payload


def state_signature_is_valid(state: dict[str, object]) -> bool:
    key = signing_key()
    if not key:
        return True
    signature = state.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    payload = dict(state)
    payload.pop("signature", None)
    return hmac.compare_digest(signature, _signature(payload, key))


def check_freeze(
    *,
    refresh: bool = True,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    state_url: str | None = None,
    now: datetime | None = None,
) -> FreezeDecision:
    now = now or timezone.now()
    cached_state = read_cached_state()
    if cached_state is None:
        cached_state = local_freeze_state()
    cached_decision = decision_from_state(cached_state, source="cache", now=now)

    if refresh:
        remote_url = freeze_state_url() if state_url is None else state_url.strip()
        if not remote_url:
            return cached_decision
        try:
            remote_state = fetch_remote_state(
                remote_url,
                timeout_seconds=timeout_seconds,
            )
            write_cached_state(remote_state)
        except (OSError, ValueError) as exc:
            if cached_decision.state:
                return FreezeDecision(
                    cached_decision.decision,
                    reason=cached_decision.reason,
                    source="cache-stale",
                    state=cached_decision.state,
                    error=str(exc),
                    bypass_until=cached_decision.bypass_until,
                )
            return FreezeDecision("allow", source="remote-error", error=str(exc))
        remote_decision = decision_from_state(remote_state, source="remote", now=now)
        return remote_decision

    return cached_decision


def decision_from_state(
    state: dict[str, object] | None,
    *,
    source: str,
    now: datetime | None = None,
) -> FreezeDecision:
    if not state:
        return FreezeDecision("allow", source=source)
    if not state_signature_is_valid(state):
        return FreezeDecision("allow", source=f"{source}-invalid-signature", state=state)
    now = now or timezone.now()
    generated_at = _parse_timestamp(state.get("generated_at"))
    max_age_seconds = state.get("max_age_seconds")
    if (
        generated_at is not None
        and isinstance(max_age_seconds, (int, float))
        and max_age_seconds >= 0
        and (now - generated_at).total_seconds() > float(max_age_seconds)
    ):
        return FreezeDecision("allow", source=f"{source}-stale", state=state)
    if not state.get("active"):
        return FreezeDecision("allow", source=source, state=state)
    ends_at = _parse_timestamp(state.get("ends_at"))
    starts_at = _parse_timestamp(state.get("starts_at")) or now
    if ends_at is None or now < starts_at or now >= ends_at:
        return FreezeDecision("allow", source=source, state=state)
    reason = str(state.get("reason") or "Codex Feature Freeze is active.")
    bypass_until = emergency_bypass_until()
    if bypass_until and now < bypass_until:
        bypass_reason = emergency_bypass_reason()
        message = (
            f"Emergency Codex freeze bypass active until {_isoformat(bypass_until)}"
            f": {bypass_reason}"
        )
        logger.warning(message)
        return FreezeDecision(
            "allow",
            reason=message,
            source=f"{source}-emergency-bypass",
            state=state,
            bypass_until=bypass_until,
        )
    return FreezeDecision(
        "refuse",
        reason=f"{reason} Freeze ends at {_isoformat(ends_at)}.",
        source=source,
        state=state,
    )


def emergency_bypass_until() -> datetime | None:
    return _parse_timestamp(os.environ.get(EMERGENCY_BYPASS_UNTIL_ENV, "").strip())


def emergency_bypass_reason() -> str:
    return (
        os.environ.get(EMERGENCY_BYPASS_REASON_ENV, "").strip()
        or "operator emergency override"
    )


def read_cached_state(path: Path | None = None) -> dict[str, object] | None:
    target = path or cache_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_cached_state(state: dict[str, object], path: Path | None = None) -> None:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=target.parent,
            encoding="utf-8",
            delete=False,
        ) as temp_file:
            json.dump(state, temp_file, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_name = temp_file.name
        Path(temp_name).replace(target)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def fetch_remote_state(url: str, *, timeout_seconds: float) -> dict[str, object]:
    if not url:
        raise ValueError("Freeze state URL is not configured.")
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Freeze state response was not a JSON object.")
    if not state_signature_is_valid(payload):
        raise ValueError("Freeze state signature is invalid.")
    return payload


def _signature(state: dict[str, object], key: str) -> str:
    message = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
