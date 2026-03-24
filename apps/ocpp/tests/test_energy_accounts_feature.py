import pytest
from channels.db import database_sync_to_async
from django.urls import reverse

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger, PublicConnectorPage


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorize_requires_account_when_energy_accounts_enabled():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-EA-AUTH",
        require_rfid=True,
    )
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "disabled"}},
        },
    )
    await database_sync_to_async(RFID.objects.create)(rfid="EA001", allowed=True)

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._handle_authorize_action(
        {"idTag": "EA001"},
        "msg-auth-energy",
        "",
        "",
    )

    assert result == {"idTagInfo": {"status": "Invalid"}}


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorize_accepts_account_without_credits_when_parameter_disabled():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-EA-ACCOUNT",
        require_rfid=True,
    )
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "disabled"}},
        },
    )
    tag = await database_sync_to_async(RFID.objects.create)(rfid="EA002", allowed=True)
    account = await database_sync_to_async(CustomerAccount.objects.create)(name="EA002ACC")
    await database_sync_to_async(account.rfids.add)(tag)

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._handle_authorize_action(
        {"idTag": "EA002"},
        "msg-auth-energy-ok",
        "",
        "",
    )

    assert result == {"idTagInfo": {"status": "Accepted"}}


@pytest.mark.django_db
def test_public_connector_page_prompts_account_creation_when_enabled(client):
    Feature.objects.update_or_create(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "disabled"}},
        },
    )
    charger = Charger.objects.create(charger_id="CP-EA-PAGE", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.get(reverse("ocpp:public-connector-page", args=[page.slug]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Create Account" in content
    assert "Authenticate to Charge" in content
