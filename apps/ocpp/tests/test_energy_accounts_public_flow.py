import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.features.models import Feature
from apps.ocpp.models import Charger, PublicConnectorPage

pytestmark = pytest.mark.django_db


def _enable_energy_accounts() -> None:
    Feature.objects.update_or_create(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"credits_required": "disabled"}},
        },
    )


def test_public_qr_page_requires_authentication_prompt(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-ENERGY-1", connector_id=1)
    page = PublicConnectorPage.objects.get(charger=charger)

    response = client.get(reverse("ocpp:public-connector-page", args=[page.slug]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Create Account" in content
    assert "Sign in or create your energy account" in content


def test_create_account_endpoint_creates_user_and_redirects(client):
    _enable_energy_accounts()
    charger = Charger.objects.create(charger_id="CP-ENERGY-2", connector_id=1)
    PublicConnectorPage.objects.get(charger=charger)

    response = client.post(
        reverse(
            "ocpp:public-create-account-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 302
    assert response.url.endswith(
        reverse(
            "ocpp:public-connector-page-by-cid-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    user = response.wsgi_request.user
    assert user.is_authenticated
    assert hasattr(user, "customer_account")
    assert user.customer_account.rfids.exists()


def test_public_qr_resume_requires_matching_connector(client, monkeypatch):
    _enable_energy_accounts()
    first_connector = Charger.objects.create(charger_id="CP-ENERGY-3", connector_id=1)
    second_connector = Charger.objects.create(charger_id="CP-ENERGY-3", connector_id=2)
    second_page = PublicConnectorPage.objects.get(charger=second_connector)
    user = get_user_model().objects.create_user(username="energy-user")
    client.force_login(user)
    session = client.session
    session["ocpp_pending_energy_charge"] = {
        "charger_id": first_connector.charger_id,
        "connector_id": first_connector.connector_id,
        "queued_at": timezone.now().isoformat(),
    }
    session.save()
    remote_start_calls = []

    def _record_call(*, charger, user):  # noqa: ANN001 - test helper signature mirrors production helper
        remote_start_calls.append((charger.pk, user.pk))
        return True

    monkeypatch.setattr("apps.ocpp.views.public.request_remote_start_for_user", _record_call)

    response = client.get(
        reverse(
            "ocpp:public-connector-page",
            args=[second_page.slug],
        )
    )

    assert response.status_code == 200
    assert remote_start_calls == []


def test_public_qr_page_shows_support_contact_details(client):
    charger = Charger.objects.create(charger_id="CP-ENERGY-4", connector_id=1)
    page = PublicConnectorPage.objects.get(charger=charger)
    page.support_phone = "+15551234567"
    page.support_whatsapp = "+15557654321"
    page.support_email = "support@example.com"
    page.support_url = "https://example.com/support"
    page.save(
        update_fields=[
            "support_phone",
            "support_whatsapp",
            "support_email",
            "support_url",
        ]
    )

    response = client.get(reverse("ocpp:public-connector-page", args=[page.slug]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "+15551234567" in content
    assert "+15557654321" in content
    assert "support@example.com" in content
    assert "https://example.com/support" in content
