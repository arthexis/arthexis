import datetime

import pytest
from django.utils import timezone

from apps.ocpp.views import common
from apps.ocpp.status_display import STATUS_BADGE_MAP


class _ChargerWithoutHelper:
    def __init__(self, *, status_ts=None, heartbeat=None, raise_attr=False):
        self.last_status_timestamp = status_ts
        self.last_heartbeat = heartbeat
        self._raise_attr = raise_attr

    @property
    def last_seen(self):
        if self._raise_attr:
            raise AttributeError("last_seen")
        return None


class _NodeStub:
    def __init__(self, *, last_updated=None, last_seen=None, pk=1):
        self.last_updated = last_updated
        self.last_seen = last_seen
        self.pk = pk


class _ConnectorStub:
    def __init__(self, *, node_origin=None):
        self.node_origin = node_origin


class _ChargerStub:
    def __init__(
        self,
        *,
        charger_id="CP-1",
        connector_id=1,
        last_status="",
        last_error_code="",
        last_status_timestamp=None,
        last_heartbeat=None,
    ):
        self.charger_id = charger_id
        self.connector_id = connector_id
        self.last_status = last_status
        self.last_error_code = last_error_code
        self.last_status_timestamp = last_status_timestamp
        self.last_heartbeat = last_heartbeat


def test_charger_last_seen_prefers_status_timestamp(monkeypatch):
    timestamp = timezone.now() - datetime.timedelta(minutes=5)
    charger = _ChargerWithoutHelper(status_ts=timestamp)

    assert common._charger_last_seen(charger) == timestamp


def test_charger_last_seen_falls_back_to_heartbeat(monkeypatch):
    heartbeat = timezone.now() - datetime.timedelta(minutes=10)
    charger = _ChargerWithoutHelper(status_ts=None, heartbeat=heartbeat)

    assert common._charger_last_seen(charger) == heartbeat


def test_charger_last_seen_handles_attribute_error(monkeypatch):
    heartbeat = timezone.now()
    charger = _ChargerWithoutHelper(status_ts=None, heartbeat=heartbeat, raise_attr=True)

    assert common._charger_last_seen(charger) == heartbeat


def test_is_untracked_origin_uses_last_updated():
    reference_time = timezone.now()
    active_delta = datetime.timedelta(minutes=5)
    origin = _NodeStub(last_updated=reference_time - datetime.timedelta(minutes=1))
    connector = _ConnectorStub(node_origin=origin)

    assert (
        common._is_untracked_origin(
            connector,
            local_node=None,
            reference_time=reference_time,
            active_delta=active_delta,
        )
        is False
    )


def test_is_untracked_origin_flags_stale_last_updated():
    reference_time = timezone.now()
    active_delta = datetime.timedelta(minutes=5)
    origin = _NodeStub(last_updated=reference_time - datetime.timedelta(minutes=10))
    connector = _ConnectorStub(node_origin=origin)

    assert (
        common._is_untracked_origin(
            connector,
            local_node=None,
            reference_time=reference_time,
            active_delta=active_delta,
        )
        is True
    )


def test_charger_state_uses_recent_heartbeat_when_disconnected(monkeypatch, settings):
    settings.NODE_LAST_SEEN_ACTIVE_DELTA = datetime.timedelta(minutes=5)
    recent = timezone.now() - datetime.timedelta(minutes=1)
    charger = _ChargerStub(last_heartbeat=recent)
    monkeypatch.setattr(common.store, "is_connected", lambda *_args, **_kwargs: False)

    label, color = common._charger_state(charger, tx_obj=None)

    assert (label, color) == STATUS_BADGE_MAP["available"]


def test_charger_state_offline_when_heartbeat_stale(monkeypatch, settings):
    settings.NODE_LAST_SEEN_ACTIVE_DELTA = datetime.timedelta(minutes=5)
    stale = timezone.now() - datetime.timedelta(minutes=10)
    charger = _ChargerStub(last_heartbeat=stale)
    monkeypatch.setattr(common.store, "is_connected", lambda *_args, **_kwargs: False)

    label, color = common._charger_state(charger, tx_obj=None)

    assert str(label) == "Offline"
    assert color == "grey"


@pytest.mark.django_db
def test_connector_overview_uses_active_transaction_fallback(monkeypatch):
    """Ensure connector summaries use persisted active sessions when needed."""

    charger = common.Charger.objects.create(
        charger_id="CP-FALLBACK",
        connector_id=1,
        last_status="Available",
        last_error_code="NoError",
    )
    common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now(),
    )

    monkeypatch.setattr(common.store, "get_transaction", lambda *_args, **_kwargs: None)

    overview = common._connector_overview(charger, connectors=[charger])

    assert overview[0]["status"] == STATUS_BADGE_MAP["charging"][0]
    assert overview[0]["color"] == STATUS_BADGE_MAP["charging"][1]


@pytest.mark.django_db
def test_active_transaction_ignores_open_row_when_newer_session_exists(monkeypatch):
    """Regression: stale open sessions should not force a charging badge."""

    charger = common.Charger.objects.create(
        charger_id="CP-STUCK",
        connector_id=2,
        last_status="Charging",
        last_error_code="NoError",
    )
    stale_open = common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now() - datetime.timedelta(hours=9),
        received_start_time=timezone.now() - datetime.timedelta(hours=9),
    )
    common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now() - datetime.timedelta(hours=3),
        received_start_time=timezone.now() - datetime.timedelta(hours=3),
        stop_time=timezone.now() - datetime.timedelta(hours=2, minutes=50),
    )

    monkeypatch.setattr(common.store, "get_transaction", lambda *_args, **_kwargs: None)

    assert common._is_superseded_open_transaction(charger, stale_open) is True
    assert common._active_transaction_for_charger(charger) is None


@pytest.mark.django_db
def test_active_transaction_keeps_open_row_when_it_is_latest(monkeypatch):
    """Open sessions remain active when no newer transaction exists."""

    charger = common.Charger.objects.create(
        charger_id="CP-LIVE",
        connector_id=1,
        last_status="Charging",
        last_error_code="NoError",
    )
    active_tx = common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now() - datetime.timedelta(minutes=10),
        received_start_time=timezone.now() - datetime.timedelta(minutes=10),
    )

    monkeypatch.setattr(common.store, "get_transaction", lambda *_args, **_kwargs: None)

    assert common._is_superseded_open_transaction(charger, active_tx) is False
    assert common._active_transaction_for_charger(charger) == active_tx


@pytest.mark.django_db
def test_active_transaction_falls_back_to_db_open_session_when_cached_row_is_superseded(monkeypatch):
    """Regression: superseded cache rows should not hide a newer persisted open transaction."""

    charger = common.Charger.objects.create(
        charger_id="CP-CACHE-DRIFT",
        connector_id=3,
        last_status="Charging",
        last_error_code="NoError",
    )
    stale_cached_tx = common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now() - datetime.timedelta(hours=4),
        received_start_time=timezone.now() - datetime.timedelta(hours=4),
    )
    fresh_open_tx = common.Transaction.objects.create(
        charger=charger,
        start_time=timezone.now() - datetime.timedelta(minutes=5),
        received_start_time=timezone.now() - datetime.timedelta(minutes=5),
    )

    monkeypatch.setattr(common.store, "get_transaction", lambda *_args, **_kwargs: stale_cached_tx)

    assert common._is_superseded_open_transaction(charger, stale_cached_tx) is True
    assert common._active_transaction_for_charger(charger) == fresh_open_tx
