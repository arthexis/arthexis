import datetime

import pytest
from django.utils import timezone

from apps.ocpp.views import common


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
