"""Tests for charger changelist stats helpers."""

from datetime import datetime

import pytest
from django.contrib.admin.sites import AdminSite
from django.utils import timezone

from apps.ocpp.admin import ChargerAdmin
from apps.ocpp.models import Charger

pytestmark = pytest.mark.django_db


def test_today_range_returns_bounds_for_current_day():
    """Today range helper returns beginning and ending datetimes."""
    admin = ChargerAdmin(Charger, AdminSite())

    start, end = admin._today_range()

    assert isinstance(start, datetime)
    assert isinstance(end, datetime)
    assert timezone.localtime(start).date() == timezone.localdate()
    assert end > start
