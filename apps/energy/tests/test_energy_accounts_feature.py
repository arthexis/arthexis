import pytest
from channels.db import database_sync_to_async
from django.contrib.auth import BACKEND_SESSION_KEY, get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger, PublicConnectorPage, Transaction
from apps.users.backends import LocalhostAdminBackend


def _enable_energy_accounts() -> None:
    Feature.objects.update_or_create(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "disabled"}},
        },
    )
    cache.delete("feature-enabled:energy-accounts")
    cache.delete("feature-parameter:energy-accounts:energy_credits_required")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorize_requires_account_when_energy_accounts_enabled():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-EA-AUTH",
        require_rfid=True,
    )
    await database_sync_to_async(_enable_energy_accounts)()
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

    assert result["idTagInfo"]["status"] == "Invalid"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorize_accepts_account_without_credits_when_parameter_disabled():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-EA-ACCOUNT",
        require_rfid=True,
    )
    await database_sync_to_async(_enable_energy_accounts)()
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

    assert result["idTagInfo"]["status"] == "Accepted"


@pytest.mark.django_db
def test_public_connector_page_prompts_account_creation_when_enabled(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-EA-PAGE", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.get(reverse("ocpp:public-connector-page", args=[page.slug]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Create Account" in content
    assert "Authenticate to Charge" in content


@pytest.mark.django_db
def test_public_connector_page_create_account_creates_user_and_account(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-EA-CREATE", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.post(
        reverse("ocpp:public-connector-page-create-account", args=[page.slug]),
        data={
            "username": "new-energy-user",
            "email": "energy@example.com",
            "password": "safe-password-123",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "ocpp:charger-page-connector", args=[charger.charger_id, charger.connector_slug]
    )
    user = get_user_model().objects.get(username="new-energy-user")
    assert CustomerAccount.objects.filter(user=user).exists()
    localhost_backend = f"{LocalhostAdminBackend.__module__}.{LocalhostAdminBackend.__name__}"
    assert client.session[BACKEND_SESSION_KEY] != localhost_backend


@pytest.mark.django_db
def test_public_connector_page_create_account_rejects_hidden_charger(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(
        charger_id="CP-EA-HIDDEN",
        connector_id=1,
        public_display=False,
    )
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.post(
        reverse("ocpp:public-connector-page-create-account", args=[page.slug]),
        data={
            "username": "blocked-energy-user",
            "email": "blocked@example.com",
            "password": "safe-password-123",
        },
    )

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(AUTHENTICATION_BACKENDS=["apps.users.backends.LocalhostAdminBackend"])
def test_public_connector_page_create_account_skips_login_with_unsafe_only_backends(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-EA-UNSAFE-ONLY", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.post(
        reverse("ocpp:public-connector-page-create-account", args=[page.slug]),
        data={
            "username": "no-session-user",
            "email": "nosession@example.com",
            "password": "safe-password-123",
        },
        follow=True,
    )

    assert response.status_code == 200
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert "Account created, but you are not signed in. Please sign in to switch to the new account." in messages
    assert BACKEND_SESSION_KEY not in client.session


@pytest.mark.django_db
def test_public_connector_page_create_account_rejects_authenticated_post(client):
    _enable_energy_accounts()
    existing_user = get_user_model().objects.create_user(
        username="existing-energy-user",
        email="existing@example.com",
        password="safe-password-123",
    )
    charger = Charger.objects.create(charger_id="CP-EA-AUTHED", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)
    client.force_login(existing_user)

    response = client.post(
        reverse("ocpp:public-connector-page-create-account", args=[page.slug]),
        data={
            "username": "should-not-create",
            "email": "new@example.com",
            "password": "safe-password-123",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username="should-not-create").exists()
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert "Please sign out before creating a new account." in messages


@pytest.mark.django_db
def test_public_connector_page_create_account_rejects_password_failing_validators(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-EA-WEAK-PASSWORD", connector_id=1)
    page = PublicConnectorPage.objects.create(charger=charger, enabled=True)

    response = client.post(
        reverse("ocpp:public-connector-page-create-account", args=[page.slug]),
        data={
            "username": "weak-password-user",
            "email": "weak@example.com",
            "password": "123",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username="weak-password-user").exists()
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert "Please provide valid account details." in messages


@pytest.mark.django_db
def test_charger_account_summary_excludes_null_account_sessions(client):
    user = get_user_model().objects.create_user(
        username="no-account-user",
        email="no-account@example.com",
        password="safe-password-123",
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="CP-EA-NO-ACCOUNT", connector_id=1)
    Transaction.objects.create(
        charger=charger,
        account=None,
        start_time=timezone.now(),
    )

    response = client.get(reverse("ocpp:charger-account-summary", args=[charger.charger_id]))

    assert response.status_code == 200
    assert response.context["account"] is None
    assert list(response.context["recent_sessions"]) == []
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert "No energy account is attached to your user yet." in messages
