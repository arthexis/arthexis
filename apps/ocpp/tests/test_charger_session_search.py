"""Tests for charger session search filters and summary rendering."""

from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction


@pytest.mark.django_db
def test_charger_session_search_quick_range_filters_and_summary(client):
    """Quick ranges should filter sessions and show aggregate summary values."""

    user = get_user_model().objects.create_user(
        username="session-search-user",
        password="secret",
    )
    charger = Charger.objects.create(charger_id="SS-CP-1", connector_id=1)
    now = timezone.now()
    Transaction.objects.create(
        charger=charger,
        start_time=now - timedelta(hours=1),
        meter_start=10000,
        meter_stop=13000,
    )
    Transaction.objects.create(
        charger=charger,
        start_time=now - timedelta(days=2),
        meter_start=8000,
        meter_stop=9000,
    )
    Transaction.objects.create(
        charger=charger,
        start_time=now - timedelta(days=10),
        meter_start=6000,
        meter_stop=7000,
    )

    assert client.login(username=user.username, password="secret")

    response = client.get(
        reverse("ocpp:charger-session-search-connector", args=[charger.charger_id, "1"]),
        {"range": "last7"},
    )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "2</strong> sessions" in body
    assert "4.00</strong> kWh" in body
    assert "Last 7 days" in body


@pytest.mark.django_db
def test_charger_session_search_date_filter_still_supported(client):
    """Exact date search should continue working alongside quick ranges."""

    user = get_user_model().objects.create_user(
        username="session-search-date-user",
        password="secret",
    )
    charger = Charger.objects.create(charger_id="SS-CP-2", connector_id=1)
    now = timezone.now()
    target = now - timedelta(days=1)
    Transaction.objects.create(
        charger=charger,
        start_time=target,
        meter_start=1000,
        meter_stop=2000,
    )
    Transaction.objects.create(
        charger=charger,
        start_time=now - timedelta(days=4),
        meter_start=2000,
        meter_stop=3000,
    )

    assert client.login(username=user.username, password="secret")

    response = client.get(
        reverse("ocpp:charger-session-search-connector", args=[charger.charger_id, "1"]),
        {"date": target.date().isoformat()},
    )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "1</strong> sessions" in body
    assert "1.00</strong> kWh" in body


@pytest.mark.django_db
def test_charger_session_search_today_uses_local_day_boundaries(client, monkeypatch):
    """Today quick range should align to local timezone midnight boundaries."""

    user = get_user_model().objects.create_user(
        username="session-search-local-day-user",
        password="secret",
    )
    charger = Charger.objects.create(charger_id="SS-CP-3", connector_id=1)
    fixed_today = date(2026, 1, 15)
    monkeypatch.setattr(timezone, "localdate", lambda: fixed_today)
    current_tz = timezone.get_current_timezone()
    Transaction.objects.create(
        charger=charger,
        start_time=timezone.make_aware(datetime(2026, 1, 15, 0, 30), current_tz),
        meter_start=1000,
        meter_stop=2000,
    )
    Transaction.objects.create(
        charger=charger,
        start_time=timezone.make_aware(datetime(2026, 1, 14, 23, 30), current_tz),
        meter_start=2000,
        meter_stop=3000,
    )

    assert client.login(username=user.username, password="secret")

    response = client.get(
        reverse("ocpp:charger-session-search-connector", args=[charger.charger_id, "1"]),
        {"range": "today"},
    )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "1</strong> sessions" in body
    assert "Today" in body


@pytest.mark.django_db
def test_charger_session_search_invalid_range_without_date_is_safe(client):
    """Unsupported quick range values should not raise server errors."""

    user = get_user_model().objects.create_user(
        username="session-search-invalid-range-user",
        password="secret",
    )
    charger = Charger.objects.create(charger_id="SS-CP-4", connector_id=1)
    assert client.login(username=user.username, password="secret")

    response = client.get(
        reverse("ocpp:charger-session-search-connector", args=[charger.charger_id, "1"]),
        {"range": "not-a-range"},
    )

    assert response.status_code == 200
    assert "No sessions found." in response.content.decode("utf-8")
