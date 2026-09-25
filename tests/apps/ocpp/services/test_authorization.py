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


def test_open_policy_accepts_unknown_cards_and_records_the_decision(
    authorization_context,
) -> None:
    open_charger, _, _ = authorization_context

    result = authorize_id_tag(charger=open_charger, id_tag="guest-card")

    assert result.accepted
    assert result.card is None
    attempt = AuthorizationAttempt.objects.get()
    assert attempt.accepted
    assert attempt.reason == "open_policy"


def test_restricted_policy_accepts_only_active_matching_credentials(
    authorization_context,
) -> None:
    _, restricted_charger, card = authorization_context

    rejected = authorize_id_tag(
        charger=restricted_charger,
        id_tag="guest-card",
    )
    accepted = authorize_id_tag(
        charger=restricted_charger,
        id_tag=card.ocpp_id_tag,
    )

    assert not rejected.accepted
    assert rejected.reason == "unknown_or_inactive_credential"
    assert accepted.accepted
    assert accepted.card == card


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
