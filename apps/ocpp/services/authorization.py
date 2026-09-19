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
    """Apply the retained logical-card and direct-account authorization policy."""
    card = CardCredential.objects.filter(ocpp_id_tag=id_tag, active=True).first()
    account = CustomerAccount.objects.filter(ocpp_id_tag=id_tag, active=True).first()
    if card is not None:
        account = card.account or account

    known_credential = account is not None or card is not None
    accepted = (
        known_credential or charger.authorization_mode == Charger.AuthorizationMode.OPEN
    )
    reason = "accepted" if known_credential else _authorization_reason(accepted)
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


def _authorization_reason(accepted: bool) -> str:
    return "open_policy" if accepted else "unknown_or_inactive_credential"
