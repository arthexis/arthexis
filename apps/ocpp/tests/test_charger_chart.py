"""Tests for charger chart payload helpers and JSON endpoints."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, MeterValue, Transaction
from apps.ocpp.services import build_charger_chart_payload


@pytest.mark.django_db
def test_build_charger_chart_payload_returns_single_connector_dataset():
    """Service should return chart labels and values for a connector session."""

    user = get_user_model().objects.create_user(
        username="chart-user",
        password="secret",
    )
    charger = Charger.objects.create(charger_id="GQL-CP-1", connector_id=1)
    start = timezone.make_aware(datetime(2025, 1, 1, 10, 0, 0))
    tx = Transaction.objects.create(
        charger=charger,
        start_time=start,
        meter_start=100000,
        meter_stop=102000,
    )
    MeterValue.objects.create(
        charger=charger,
        transaction=tx,
        connector_id=1,
        timestamp=start + timedelta(minutes=5),
        context="Sample.Periodic",
        energy=Decimal("100.500"),
    )
    MeterValue.objects.create(
        charger=charger,
        transaction=tx,
        connector_id=1,
        timestamp=start + timedelta(minutes=10),
        context="Sample.Periodic",
        energy=Decimal("101.000"),
    )

    payload = build_charger_chart_payload(
        user=user,
        cid="GQL-CP-1",
        connector="1",
    )

    assert len(payload["labels"]) == 2
    assert len(payload["datasets"]) == 1
    assert payload["datasets"][0]["connector_id"] == 1
    assert payload["datasets"][0]["values"] == [0.5, 1.0]


@pytest.mark.django_db
def test_charger_status_chart_endpoint_returns_chart_data(client):
    """JSON endpoint should expose charger chart data for authenticated users."""

    get_user_model().objects.create_user(username="chart-client", password="secret")
    charger = Charger.objects.create(charger_id="GQL-CP-2", connector_id=1)
    start = timezone.now()
    tx = Transaction.objects.create(
        charger=charger,
        start_time=start,
        meter_start=50000,
    )
    MeterValue.objects.create(
        charger=charger,
        transaction=tx,
        connector_id=1,
        timestamp=start + timedelta(minutes=1),
        context="Sample.Periodic",
        energy=Decimal("50.250"),
    )

    assert client.login(username="chart-client", password="secret")

    response = client.get(
        reverse("ocpp:charger-status-chart-connector", args=["GQL-CP-2", "1"]),
        {"session": str(tx.pk)},
    )

    assert response.status_code == 200
    payload = response.json()
    datasets = payload["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["connector_id"] == 1
