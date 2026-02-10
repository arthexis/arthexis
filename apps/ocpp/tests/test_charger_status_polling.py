"""Tests for charger status polling behavior in the status page context."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction


@pytest.mark.django_db
def test_status_view_disables_polling_without_active_session(client):
    """Status polling should be disabled when no live session exists."""

    user = get_user_model().objects.create_user(
        username="status-no-session", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-NO-TX")

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert response.context["status_should_poll"] is False


@pytest.mark.django_db
def test_status_view_enables_polling_with_active_session(client):
    """Status polling should be enabled while a live transaction is active."""

    user = get_user_model().objects.create_user(
        username="status-with-session", password="pass"
    )
    client.force_login(user)
    charger = Charger.objects.create(charger_id="STATUS-WITH-TX")
    Transaction.objects.create(charger=charger, start_time=timezone.now())

    response = client.get(reverse("ocpp:charger-status", args=[charger.charger_id]))

    assert response.status_code == 200
    assert response.context["status_should_poll"] is True
