from datetime import datetime, timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.models import Charger
from tests.apps.ocpp.builders import charger, connection, station_model, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def fleet_charger():
    model = station_model(protocol="ocpp1.6")
    selected = charger("charger-1", station=model)
    connection(selected, channel_name="fleet-channel")
    transaction(
        selected,
        "transaction-last",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    transaction(
        selected,
        "transaction-active",
        started_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
    )
    return selected


def test_command_renders_the_app_wide_fleet_snapshot(fleet_charger) -> None:
    output = StringIO()

    call_command("fleet", stdout=output)

    rendered = output.getvalue()
    for value in (
        "Fleet snapshot:",
        "Charger",
        "Enabled",
        "Connection",
        "State",
        "Protocol",
        "Active TX",
        "Last TX",
        "Last contact",
        "charger-1",
        "yes",
        "connected",
        "charging",
        "ocpp1.6",
        "transaction-active",
        "transaction-last",
    ):
        assert value in rendered


def test_command_does_not_accept_model_mutation_verbs(fleet_charger) -> None:
    with pytest.raises(CommandError, match="unrecognized arguments: reset"):
        call_command("fleet", "reset", "--charger", fleet_charger.identity)


def test_command_is_one_shot_and_handles_an_empty_fleet() -> None:
    Charger.objects.all().delete()
    output = StringIO()

    call_command("fleet", stdout=output)

    rendered = output.getvalue()
    assert rendered.count("Fleet snapshot:") == 1
    assert "No chargers found." in rendered


@pytest.mark.parametrize(
    "arguments",
    [
        ("--enabled", "--disabled"),
        ("--connected", "--disconnected"),
        ("--charging", "--idle"),
        ("--charging", "--unresolved"),
    ],
)
def test_opposing_filters_are_mutually_exclusive(arguments) -> None:
    with pytest.raises(CommandError, match="not allowed with argument"):
        call_command("fleet", *arguments)


def test_detail_flag_requests_the_richer_view(fleet_charger) -> None:
    output = StringIO()

    call_command("fleet", "--detail", stdout=output)

    rendered = output.getvalue()
    for value in (
        "Connectors",
        "Active since",
        "Last stopped",
        "Energy total",
        "Recovery unresolved",
        "Energy unresolved",
        "Authority cutover",
        "Historical TX",
        "Historical open",
        "Historical oldest",
        "Historical latest",
    ):
        assert value in rendered


def test_historical_filter_and_detail_surface_retained_history(fleet_charger) -> None:
    historical_charger = charger("charger-historical")
    connection(historical_charger, channel_name="historical-channel")
    historical_charger.authority_cutover_at = datetime(
        2026, 9, 22, 12, tzinfo=timezone.utc
    )
    historical_charger.save(update_fields=("authority_cutover_at",))
    transaction(
        historical_charger,
        "historical-complete",
        started_at=datetime(2023, 1, 1, 10, tzinfo=timezone.utc),
        stopped_at=datetime(2023, 1, 1, 11, tzinfo=timezone.utc),
        historical=True,
    )
    transaction(
        historical_charger,
        "historical-open",
        started_at=datetime(2023, 1, 2, 10, tzinfo=timezone.utc),
        historical=True,
    )
    output = StringIO()

    call_command("fleet", "--historical", "--detail", stdout=output)

    rendered = output.getvalue()
    assert "charger-historical" in rendered
    assert "charger-1" not in rendered
    for value in ("Historical TX", "Historical open", "2", "1", "2023-01-01", "2023-01-02"):
        assert value in rendered


def test_unresolved_filter_and_state_are_visible_to_operators(fleet_charger) -> None:
    uncertain = Charger.objects.get(pk=fleet_charger.pk).transactions.get(
        remote_id="transaction-active"
    )
    uncertain.recovery_state = uncertain.RecoveryState.UNRESOLVED
    uncertain.save(update_fields=("recovery_state",))
    output = StringIO()

    call_command("fleet", "--unresolved", stdout=output)

    rendered = output.getvalue()
    assert "charger-1" in rendered
    assert "unresolved" in rendered
    assert "charging" not in rendered
