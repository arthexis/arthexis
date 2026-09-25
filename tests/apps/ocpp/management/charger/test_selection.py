from datetime import datetime, timezone

import pytest
from django.core.management.base import CommandError

from apps.ocpp.management.charger.selection import select_chargers
from tests.apps.ocpp.builders import charger, connection, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def active_charger():
    selected = charger("charger-1")
    connection(selected, channel_name="fleet-channel")
    transaction(
        selected,
        "transaction-active",
        started_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
    )
    return selected


def test_rejects_unknown_duplicate_and_mixed_targets(active_charger) -> None:
    with pytest.raises(CommandError, match="Unknown charger"):
        list(select_chargers(identities=["missing"], select_all=False))
    with pytest.raises(CommandError, match="only once"):
        list(
            select_chargers(
                identities=[active_charger.identity, active_charger.identity],
                select_all=False,
            )
        )
    with pytest.raises(CommandError, match="--all by itself"):
        list(
            select_chargers(
                identities=[active_charger.identity],
                select_all=True,
            )
        )


def test_filters_compose_across_dimensions(active_charger) -> None:
    idle = charger("charger-idle")
    connection(idle, channel_name="idle-channel")
    disabled = charger("charger-disabled", active=False)
    unresolved = charger("charger-unresolved")
    connection(unresolved, channel_name="unresolved-channel")
    unresolved_transaction = transaction(
        unresolved,
        "transaction-unresolved",
        started_at=datetime(2026, 1, 1, 3, tzinfo=timezone.utc),
    )
    unresolved_transaction.recovery_state = unresolved_transaction.RecoveryState.UNRESOLVED
    unresolved_transaction.save(update_fields=("recovery_state",))

    enabled_connected = list(
        select_chargers(
            identities=[],
            select_all=False,
            filters=("enabled", "connected"),
        )
    )
    assert active_charger in enabled_connected
    assert idle in enabled_connected
    assert disabled not in enabled_connected

    idle_only = list(
        select_chargers(
            identities=[],
            select_all=False,
            filters=("idle",),
        )
    )
    assert idle in idle_only
    assert active_charger not in idle_only
    assert unresolved not in idle_only

    unresolved_only = list(
        select_chargers(
            identities=[],
            select_all=False,
            filters=("unresolved",),
        )
    )
    assert unresolved_only == [unresolved]


def test_rejects_unknown_filter_names() -> None:
    with pytest.raises(CommandError, match="Unknown fleet filter"):
        list(
            select_chargers(
                identities=[],
                select_all=False,
                filters=("reset",),
            )
        )
