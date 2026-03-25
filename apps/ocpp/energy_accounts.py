"""Energy-account charging helpers for public QR flows and OCPP auth."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone as dt_timezone

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.features.parameters import get_feature_parameter
from apps.features.utils import is_suite_feature_enabled

from . import store

ENERGY_ACCOUNTS_FEATURE_SLUG = "energy-accounts"
ENERGY_CREDITS_REQUIRED_PARAMETER = "credits_required"
PENDING_ENERGY_CHARGE_SESSION_KEY = "ocpp_pending_energy_charge"
PENDING_ENERGY_CHARGE_TTL_SECONDS = 15 * 60


def energy_accounts_enabled(*, default: bool = False) -> bool:
    """Return whether energy-account mode is enabled."""

    return is_suite_feature_enabled(ENERGY_ACCOUNTS_FEATURE_SLUG, default=default)


def energy_credits_required() -> bool:
    """Return whether account balance is required for authorization."""

    value = get_feature_parameter(
        ENERGY_ACCOUNTS_FEATURE_SLUG,
        ENERGY_CREDITS_REQUIRED_PARAMETER,
        fallback="disabled",
    )
    return value == "enabled"


def can_authorize_account(account: CustomerAccount | None) -> bool:
    """Return whether an account can start a session under current policy."""

    if account is None:
        return False
    if not energy_credits_required():
        return True
    return bool(account.can_authorize())


def get_or_create_energy_account_for_user(user) -> CustomerAccount:
    """Return the user's account, creating one when missing."""

    account = getattr(user, "customer_account", None)
    if account is not None:
        return account
    base_name = (user.username or f"USER-{user.pk}").upper()
    candidate = base_name
    suffix = 2
    while CustomerAccount.objects.filter(name=candidate).exists():
        candidate = f"{base_name}-{suffix}"
        suffix += 1
    return CustomerAccount.objects.create(name=candidate, user=user)


def _virtual_rfid_hex_for_user(user) -> str:
    seed = f"{user.pk}:{user.username or ''}"
    return salted_hmac(
        "apps.ocpp.energy_accounts.virtual_rfid",
        seed,
        algorithm="sha256",
    ).hexdigest()[:16].upper()


def get_or_create_virtual_rfid_for_user(user) -> RFID:
    """Return a deterministic RFID token linked to the user's energy account."""

    rfid_value = _virtual_rfid_hex_for_user(user)
    tag, _created = RFID.objects.get_or_create(
        rfid=rfid_value,
        defaults={
            "allowed": True,
            "released": True,
            "custom_label": f"Energy account {user.pk}",
        },
    )
    updates: list[str] = []
    if not tag.allowed:
        tag.allowed = True
        updates.append("allowed")
    if not tag.released:
        tag.released = True
        updates.append("released")
    if updates:
        tag.save(update_fields=sorted(updates))

    account = get_or_create_energy_account_for_user(user)
    account.rfids.add(tag)
    return tag


def create_energy_account_user(*, username_prefix: str = "energy"):
    """Create and return a new lightweight user for QR onboarding."""

    User = get_user_model()
    username = f"{username_prefix}-{uuid.uuid4().hex[:10]}"
    user = User.objects.create_user(username=username)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    get_or_create_virtual_rfid_for_user(user)
    return user


def queue_pending_energy_charge(request, *, charger_id: str, connector_id: int | None) -> None:
    """Store pending charger context in the session for post-login auto start."""

    request.session[PENDING_ENERGY_CHARGE_SESSION_KEY] = {
        "charger_id": charger_id,
        "connector_id": connector_id,
        "queued_at": timezone.now().isoformat(),
    }
    request.session.modified = True


def pop_pending_energy_charge(request) -> dict[str, object] | None:
    """Pop and validate a pending charge payload from session state."""

    payload = request.session.pop(PENDING_ENERGY_CHARGE_SESSION_KEY, None)
    request.session.modified = True
    if not isinstance(payload, dict):
        return None
    queued_at = payload.get("queued_at")
    queued_dt = timezone.now()
    if isinstance(queued_at, str):
        try:
            parsed = datetime.fromisoformat(queued_at)
        except ValueError:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, dt_timezone.utc)
        queued_dt = parsed
    elapsed = (timezone.now() - queued_dt).total_seconds()
    if elapsed > PENDING_ENERGY_CHARGE_TTL_SECONDS:
        return None
    return payload


def request_remote_start_for_user(*, charger, user) -> bool:
    """Request remote start for the authenticated user's linked account."""

    tag = get_or_create_virtual_rfid_for_user(user)
    ws = store.get_connection(charger.charger_id, charger.connector_id)
    if ws is None:
        return False

    action = "RequestStartTransaction"
    remote_start_id = int(uuid.uuid4().int % 1_000_000_000)
    payload: dict[str, object] = {
        "idToken": {"idToken": tag.rfid, "type": "Central"},
        "remoteStartId": remote_start_id,
    }
    if charger.connector_id is not None:
        payload["evseId"] = int(charger.connector_id)

    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, action, payload])
    async_to_sync(ws.send)(msg)
    requested_at = timezone.now()
    metadata = {
        "action": action,
        "charger_id": charger.charger_id,
        "connector_id": charger.connector_id,
        "log_key": store.identity_key(charger.charger_id, charger.connector_id),
        "id_token": tag.rfid,
        "id_token_type": "Central",
        "remote_start_id": remote_start_id,
        "requested_at": requested_at,
    }
    store.register_pending_call(message_id, metadata)
    store.register_transaction_request(message_id, metadata)
    return True
