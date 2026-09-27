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
    direct_account = CustomerAccount.objects.filter(
        ocpp_id_tag=id_tag,
        active=True,
    ).first()
    account = card.account if card is not None and card.account is not None else direct_account

    trusted_card = card is not None and not card.auto_learned
    known_credential = account is not None or trusted_card
    open_policy = charger.authorization_mode == Charger.AuthorizationMode.OPEN

    if open_policy:
        accepted = True
        if card is None:
            card, newly_learned = _learn_unknown_card(id_tag)
            if card is not None and card.account is not None:
                account = card.account
            if newly_learned:
                reason = "learned_open_policy"
            elif card is not None and not card.auto_learned:
                reason = "accepted"
            else:
                reason = "open_policy"
        else:
            reason = "accepted" if known_credential else "open_policy"
    else:
        accepted = known_credential
        reason = "accepted" if accepted else "unknown_or_inactive_credential"

    AuthorizationAttempt.objects.create(
        card=card if card is not None and card.active else None,
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
        card=card if card is not None and card.active else None,
        account=account,
    )


def _learn_unknown_card(id_tag: str) -> tuple[CardCredential | None, bool]:
    existing = CardCredential.objects.filter(external_id=id_tag).first()
    if existing is not None:
        if not existing.active:
            return None, False
        if not existing.ocpp_id_tag:
            existing.ocpp_id_tag = id_tag
            existing.save(update_fields=("ocpp_id_tag",))
        return existing, False

    return (
        CardCredential.objects.create(
            external_id=id_tag,
            ocpp_id_tag=id_tag,
            auto_learned=True,
        ),
        True,
    )
