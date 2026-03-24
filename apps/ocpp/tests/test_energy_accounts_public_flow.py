import pytest

from django.urls import reverse

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
