from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.ocpp.utils import _parse_ocpp_timestamp


@pytest.mark.parametrize("value", [None, "", 0])
def test_parse_ocpp_timestamp_returns_none_for_empty_values(value):
    assert _parse_ocpp_timestamp(value) is None


def test_parse_ocpp_timestamp_preserves_aware_datetime():
    aware_dt = datetime(2024, 1, 1, 12, 30, tzinfo=ZoneInfo("UTC"))
    assert _parse_ocpp_timestamp(aware_dt) is aware_dt


def test_parse_ocpp_timestamp_parses_string_and_makes_naive_aware():
    naive_dt = datetime(2024, 2, 3, 4, 5, 6)
    tz = ZoneInfo("Asia/Tokyo")
    with timezone.override(tz):
        result = _parse_ocpp_timestamp(naive_dt)
    assert timezone.is_aware(result)
    assert result.tzinfo == tz
    assert result.replace(tzinfo=None) == naive_dt


def test_parse_ocpp_timestamp_parses_utc_string():
    timestamp_str = "2024-05-06T07:08:09Z"
    parsed = _parse_ocpp_timestamp(timestamp_str)
    assert parsed is not None
    assert timezone.is_aware(parsed)
    assert parsed == datetime(2024, 5, 6, 7, 8, 9, tzinfo=dt_timezone.utc)
