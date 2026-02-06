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
    monkeypatch.setattr(common.store, "is_connected", lambda *args, **kwargs: False)

    label, color = common._charger_state(charger, tx_obj=None)

    assert (label, color) == STATUS_BADGE_MAP["available"]


def test_charger_state_offline_when_heartbeat_stale(monkeypatch, settings):
    settings.NODE_LAST_SEEN_ACTIVE_DELTA = datetime.timedelta(minutes=5)
    stale = timezone.now() - datetime.timedelta(minutes=10)
    charger = _ChargerStub(last_heartbeat=stale)
    monkeypatch.setattr(common.store, "is_connected", lambda *args, **kwargs: False)

    label, color = common._charger_state(charger, tx_obj=None)

    assert str(label) == "Offline"
    assert color == "grey"
