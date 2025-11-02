from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from core.models import EnergyAccount
from ocpp.models import Charger, Location, MeterValue, Transaction


QUERY = {
    "query": """
        query EnergySessions($filter: EnergySessionFilterInput!, $pagination: PaginationInput) {
            energySessions(filter: $filter, pagination: $pagination) {
                totalCount
                pageInfo {
                    hasNextPage
                    endCursor
                }
                edges {
                    cursor
                    node {
                        id
                        chargerId
                        connectorId
                        account
                        startedAt
                        stoppedAt
                        energyKwh
                        meterValues {
                            timestamp
                            energyKwh
                            currentImport
                            voltage
                        }
                    }
                }
            }
        }
    """,
}


@pytest.fixture()
def graphql_url() -> str:
    return reverse("graphql")


@pytest.fixture()
def export_user():
    user_model = get_user_model()
    username = f"exporter-{uuid.uuid4().hex}"
    user = user_model.objects.create_user(username, password="password")
    perms = Permission.objects.filter(codename__in=["view_transaction", "view_metervalue"])
    user.user_permissions.add(*perms)
    return user


@pytest.fixture()
def energy_data():
    MeterValue.objects.all().delete()
    Transaction.objects.all().delete()
    Charger.objects.all().delete()
    Location.objects.all().delete()
    EnergyAccount.objects.all().delete()

    now = timezone.now()

    location_main = Location.objects.create(name="Campus A")
    location_alt = Location.objects.create(name="Campus B")

    charger_main_id = f"CHARGER-{uuid.uuid4().hex[:8]}"
    charger_alt_id = f"CHARGER-{uuid.uuid4().hex[:8]}"

    charger_main = Charger.objects.create(charger_id=charger_main_id, location=location_main)
    charger_alt = Charger.objects.create(charger_id=charger_alt_id, location=location_alt)

    account = EnergyAccount.objects.create(name=f"RESEARCH-{uuid.uuid4().hex[:8]}")

    completed_start = now - timedelta(days=2)
    completed = Transaction.objects.create(
        charger=charger_main,
        account=account,
        connector_id=1,
        meter_start=1000,
        meter_stop=2500,
        start_time=completed_start,
        stop_time=completed_start + timedelta(hours=1),
    )

    MeterValue.objects.create(
        charger=charger_main,
        transaction=completed,
        connector_id=1,
        timestamp=completed.start_time + timedelta(minutes=10),
        energy=Decimal("1.1"),
        current_import=Decimal("6.5"),
        voltage=Decimal("220"),
    )
    MeterValue.objects.create(
        charger=charger_main,
        transaction=completed,
        connector_id=1,
        timestamp=completed.stop_time,
        energy=Decimal("2.6"),
        current_import=Decimal("7.1"),
        voltage=Decimal("221"),
    )

    active = Transaction.objects.create(
        charger=charger_main,
        account=account,
        connector_id=1,
        meter_start=1500,
        start_time=now - timedelta(hours=6),
        stop_time=None,
    )
    MeterValue.objects.create(
        charger=charger_main,
        transaction=active,
        connector_id=1,
        timestamp=active.start_time + timedelta(minutes=5),
        energy=Decimal("1.5"),
    )
    MeterValue.objects.create(
        charger=charger_main,
        transaction=active,
        connector_id=1,
        timestamp=active.start_time + timedelta(hours=2),
        energy=Decimal("2.4"),
    )

    incomplete_start = now - timedelta(days=4)
    incomplete = Transaction.objects.create(
        charger=charger_alt,
        connector_id=2,
        meter_stop=3000,
        start_time=incomplete_start,
        stop_time=incomplete_start + timedelta(hours=2),
    )
    MeterValue.objects.create(
        charger=charger_alt,
        transaction=incomplete,
        connector_id=2,
        timestamp=incomplete.start_time + timedelta(hours=1),
        energy=None,
    )

    return {
        "now": now,
        "location_main": location_main,
        "location_alt": location_alt,
        "charger_main": charger_main,
        "charger_alt": charger_alt,
        "account": account,
        "completed": completed,
        "active": active,
        "incomplete": incomplete,
    }


def _perform_query(client, url, user, payload):
    client.force_login(user)
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION="Token exporter-token",
    )
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    return body["data"]["energySessions"]


@pytest.mark.django_db()
def test_energy_sessions_returns_expected_payload(client, graphql_url, export_user, energy_data):
    payload = {
        **QUERY,
        "variables": {
            "filter": {
                "startTime": (energy_data["now"] - timedelta(days=7)).isoformat(),
                "locationIds": [str(energy_data["location_main"].pk)],
            }
        },
    }

    data = _perform_query(client, graphql_url, export_user, payload)

    assert data["totalCount"] == 2
    assert data["pageInfo"]["hasNextPage"] is False
    edges = data["edges"]
    assert len(edges) == 2

    newest = edges[0]["node"]
    assert newest["chargerId"] == energy_data["charger_main"].charger_id
    assert newest["account"] == energy_data["account"].name
    assert pytest.approx(newest["energyKwh"], rel=1e-3) == 0.9
    assert [mv["energyKwh"] for mv in newest["meterValues"]] == [1.5, 2.4]

    oldest = edges[1]["node"]
    assert pytest.approx(oldest["energyKwh"], rel=1e-3) == 1.5
    assert [mv["energyKwh"] for mv in oldest["meterValues"]] == [1.1, 2.6]


@pytest.mark.django_db()
def test_energy_sessions_supports_account_and_charger_filters(client, graphql_url, export_user, energy_data):
    payload = {
        **QUERY,
        "variables": {
            "filter": {
                "startTime": (energy_data["now"] - timedelta(days=7)).isoformat(),
                "chargerIds": [energy_data["charger_alt"].charger_id],
                "accountIds": [str(energy_data["account"].pk)],
            }
        },
    }

    data = _perform_query(client, graphql_url, export_user, payload)

    assert data["totalCount"] == 0
    assert data["edges"] == []


@pytest.mark.django_db()
def test_energy_sessions_pagination_after_cursor(client, graphql_url, export_user, energy_data):
    payload = {
        **QUERY,
        "variables": {
            "filter": {
                "startTime": (energy_data["now"] - timedelta(days=7)).isoformat(),
            },
            "pagination": {
                "first": 1,
            },
        },
    }

    first_page = _perform_query(client, graphql_url, export_user, payload)

    assert first_page["totalCount"] == 3
    assert first_page["pageInfo"]["hasNextPage"] is True
    cursor = first_page["pageInfo"]["endCursor"]
    assert cursor

    payload["variables"]["pagination"]["after"] = cursor
    second_page = _perform_query(client, graphql_url, export_user, payload)

    assert second_page["totalCount"] == 3
    assert second_page["pageInfo"]["hasNextPage"] is True
    assert len(second_page["edges"]) == 1
    assert second_page["edges"][0]["node"]["id"] == str(energy_data["completed"].pk)

    payload["variables"]["pagination"]["after"] = second_page["pageInfo"]["endCursor"]
    third_page = _perform_query(client, graphql_url, export_user, payload)

    assert third_page["totalCount"] == 3
    assert third_page["pageInfo"]["hasNextPage"] is False
    assert len(third_page["edges"]) == 1
    assert third_page["edges"][0]["node"]["id"] == str(energy_data["incomplete"].pk)
