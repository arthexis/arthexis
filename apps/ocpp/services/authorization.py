"""Authorization policy shared by OCPP protocol handlers."""

from dataclasses import dataclass

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount
from apps.events.services import publish_safely
from apps.ocpp.models import Charger


@dataclass(frozen=True)
class AuthorizationResult:
    accepted: bool
    reason: str
    card: CardCredential | None = None
    account: CustomerAccount | None = None


def authorize_id_tag(*, charger: Charger, id_tag: str) -> AuthorizationResult:
    """Apply charger RFID policy and learn unknown credentials in OPEN mode."""
    card = CardCredential.objects.filter(ocpp_id_tag=id_tag, active=True).first()
    account = CustomerAccount.objects.filter(ocpp_id_tag=id_tag, active=True).first()
    if card is not None:
        account = card.account or account

    known_credential = account is not None or card is not None
    open_policy = charger.authorization_mode == Charger.AuthorizationMode.OPEN
    accepted = known_credential or open_policy
    if known_credential:
        reason = "accepted"
    elif open_policy:
        learned = _learn_unknown_card(id_tag)
        card = learned or card
        account = card.account if card is not None else account
        reason = "learned_open_policy" if learned is not None else "open_policy"
    else:
        reason = "unknown_or_inactive_credential"

    AuthorizationAttempt.objects.create(
        card=card,
        presented_id=id_tag,
        accepted=accepted,
        reason=reason,
    )
    publish_safely(
        event_type="ocpp.authorization",
        producer="ocpp",
        payload={
            "charger": charger.identity,
            "id_tag": id_tag,
            "accepted": accepted,
            "reason": reason,
        },
    )
    return AuthorizationResult(
        accepted=accepted,
        reason=reason,
        card=card,
        account=account,
    )


def _learn_unknown_card(id_tag: str) -> CardCredential | None:
    existing = CardCredential.objects.filter(external_id=id_tag).first()
    if existing is not None:
        if not existing.active:
            return None
        if not existing.ocpp_id_tag:
            existing.ocpp_id_tag = id_tag
            existing.save(update_fields=("ocpp_id_tag",))
        return existing

    return CardCredential.objects.create(
        external_id=id_tag,
        ocpp_id_tag=id_tag,
    )
