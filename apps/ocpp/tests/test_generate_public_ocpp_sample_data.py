"""Tests for deterministic public OCPP sample data generation."""

import pytest
from django.core.management import call_command

from apps.ocpp.models import Charger, Transaction


pytestmark = [pytest.mark.django_db]


def test_generate_public_ocpp_sample_data_is_repeatable_with_seed_and_base_time():
    """Equal seed/base-time pairs should produce identical generated timestamps."""

    command_kwargs = {
        "chargers": 1,
        "connectors": 1,
        "transactions": 2,
        "seed": 123,
        "base_time": "2024-05-01T09:30:00+00:00",
    }

    call_command("generate_public_ocpp_sample_data", prefix="DETA", **command_kwargs)
    connector_a = Charger.objects.get(charger_id="DETA-001", connector_id=1)
    tx_a = list(
        Transaction.objects.filter(charger=connector_a)
        .order_by("start_time", "ocpp_transaction_id")
        .values_list("start_time", "stop_time", "meter_start", "meter_stop")
    )

    call_command("generate_public_ocpp_sample_data", prefix="DETB", **command_kwargs)
    connector_b = Charger.objects.get(charger_id="DETB-001", connector_id=1)
    tx_b = list(
        Transaction.objects.filter(charger=connector_b)
        .order_by("start_time", "ocpp_transaction_id")
        .values_list("start_time", "stop_time", "meter_start", "meter_stop")
    )

    assert tx_a == tx_b
    assert connector_a.last_status_timestamp == connector_b.last_status_timestamp
    assert connector_a.last_heartbeat == connector_b.last_heartbeat
