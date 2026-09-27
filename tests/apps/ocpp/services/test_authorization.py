from unittest.mock import patch

import pytest

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount
from apps.events.models import EventEnvelope
from apps.ocpp.models import Charger
from apps.ocpp.services.authorization import authorize_id_tag
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def authorization_context():
    open_charger = charger("charger-open")
    restricted_charger = charger(
        "charger-restricted",
        authorization_mode=Charger.AuthorizationMode.RESTRICTED,
    )
    card = CardCredential.objects.create(
        external_id="card-1",
        ocpp_id_tag="known-card",
    )
    return open_charger, restricted_charger, card


def test_open_policy_accepts_and_learns_unknown_cards(
    authorization_context,
) -> None:
    open_charger, _, _ = authorization_context

    result = authorize_id_tag(charger=open_charger, id_tag="guest-card")

    assert result.accepted
    assert result.card is not None
    assert result.card.external_id == "guest-card"
    assert result.card.ocpp_id_tag == "guest-card"
    assert result.card.auto_learned
    attempt = AuthorizationAttempt.objects.get()
    assert attempt.card == result.card
    assert attempt.accepted
    assert attempt.reason == "learned_open_policy"


def test_auto_learned_card_does_not_become_trusted_for_restricted_chargers(
    authorization_context,
) -> None:
    open_charger, restricted_charger, _ = authorization_context

    learned = authorize_id_tag(charger=open_charger, id_tag="guest-card")
    rejected = authorize_id_tag(charger=restricted_charger, id_tag="guest-card")

    assert learned.accepted
    assert learned.card is not None
    assert learned.card.auto_learned
    assert not rejected.accepted
    assert rejected.reason == "unknown_or_inactive_credential"


def test_explicitly_trusted_card_is_accepted_by_restricted_charger(
    authorization_context,
) -> None:
    _, restricted_charger, card = authorization_context

    accepted = authorize_id_tag(
        charger=restricted_charger,
        id_tag=card.ocpp_id_tag,
    )

    assert accepted.accepted
    assert accepted.card == card
    assert not card.auto_learned


def test_restricted_policy_rejects_unknown_credentials_without_learning(
    authorization_context,
) -> None:
    _, restricted_charger, _ = authorization_context

    rejected = authorize_id_tag(
        charger=restricted_charger,
        id_tag="unknown-card",
    )

    assert not rejected.accepted
    assert rejected.reason == "unknown_or_inactive_credential"
    assert not CardCredential.objects.filter(external_id="unknown-card").exists()


def test_open_policy_does_not_reactivate_explicitly_disabled_card() -> None:
    selected = charger("charger-open")
    disabled = CardCredential.objects.create(
        external_id="disabled-card",
        ocpp_id_tag="disabled-card",
        active=False,
    )

    result = authorize_id_tag(charger=selected, id_tag="disabled-card")

    assert result.accepted
    disabled.refresh_from_db()
    assert not disabled.active
    assert result.card is None
    assert AuthorizationAttempt.objects.get().reason == "open_policy"


def test_authorization_records_attempt_and_event() -> None:
    account = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
        ocpp_id_tag="account-tag",
    )
    card = CardCredential.objects.create(
        external_id="event-card",
        account=account,
        ocpp_id_tag="card-tag",
    )
    selected = charger("charger-event")

    result = authorize_id_tag(charger=selected, id_tag=card.ocpp_id_tag)

    assert result.accepted
    assert result.account == account
    assert AuthorizationAttempt.objects.get(presented_id="card-tag").accepted
    assert (
        EventEnvelope.objects.get(event_type="ocpp.authorization").producer
        == "ocpp"
    )


def test_authorization_hot_path_does_not_require_event_broker() -> None:
    selected = charger("charger-broker-independent")

    with patch(
        "apps.events.tasks.process_event.delay",
        side_effect=ConnectionError("broker unavailable"),
    ) as delay:
        result = authorize_id_tag(charger=selected, id_tag="guest-card")

    assert result.accepted
    assert (
        EventEnvelope.objects.get(event_type="ocpp.authorization").delivery_status
        == EventEnvelope.DeliveryStatus.PENDING
    )
    delay.assert_not_called()
