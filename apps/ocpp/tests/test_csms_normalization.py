import math
import sys

import pytest

from apps.ocpp.consumers.csms.normalization import (
    ReportChargingProfilesValidationError,
    parse_optional_int,
    validate_report_charging_profiles_payload,
)
from apps.ocpp.consumers.csms.protocol import validate_call_envelope


def _current_int_max_str_digits():
    get_int_max_str_digits = getattr(sys, "get_int_max_str_digits", None)
    if get_int_max_str_digits is None:
        return 0
    return get_int_max_str_digits()


_INT_MAX_STR_DIGITS = _current_int_max_str_digits()


def _minimum_payload(**schedule_overrides):
    schedule = {
        "chargingRateUnit": "W",
        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 5.5}],
    }
    schedule.update(schedule_overrides)
    return {
        "evseId": 1,
        "chargingProfile": {
            "id": 10,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": schedule,
        },
    }


def test_validate_report_charging_profiles_payload_parses_minimum_payload():
    result = validate_report_charging_profiles_payload(
        _minimum_payload(), connector_value=None
    )

    assert result.evse_id == 1
    assert result.profiles[0].connector_id == 1
    assert result.profiles[0].schedule.periods[0].limit == 5.5


def test_validate_report_charging_profiles_payload_preserves_zero_ids():
    payload = _minimum_payload()
    payload["evseId"] = 0
    payload["chargingProfile"]["chargingProfileId"] = 0
    payload["chargingProfile"].pop("id")

    result = validate_report_charging_profiles_payload(payload, connector_value=1)

    assert result.evse_id == 0
    assert result.profiles[0].connector_id == 0
    assert result.profiles[0].profile_id == 0


@pytest.mark.parametrize(
    "limit", [0, "NaN", "Infinity", "-Infinity", math.nan, math.inf, -math.inf]
)
def test_validate_report_charging_profiles_payload_rejects_invalid_period_limit(limit):
    payload = _minimum_payload(
        chargingSchedulePeriod=[{"startPeriod": 0, "limit": limit}]
    )

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


@pytest.mark.parametrize(
    "min_charging_rate", ["NaN", "Infinity", "-Infinity", math.nan, math.inf, -math.inf]
)
def test_validate_report_charging_profiles_payload_rejects_non_finite_min_charging_rate(
    min_charging_rate,
):
    payload = _minimum_payload(minChargingRate=min_charging_rate)

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


@pytest.mark.parametrize("duration", ["abc", 1.7])
def test_validate_report_charging_profiles_payload_rejects_malformed_duration(
    duration,
):
    payload = _minimum_payload(duration=duration)

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


def test_validate_report_charging_profiles_payload_rejects_non_boolean_tbc():
    payload = _minimum_payload()
    payload["tbc"] = "false"

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


def test_validate_report_charging_profiles_payload_rejects_boolean_stack_level():
    payload = _minimum_payload()
    payload["chargingProfile"]["stackLevel"] = True

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


def test_validate_report_charging_profiles_payload_rejects_fractional_start_period():
    payload = _minimum_payload(
        chargingSchedulePeriod=[{"startPeriod": 1.7, "limit": 5.5}]
    )

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


@pytest.mark.skipif(
    _INT_MAX_STR_DIGITS == 0,
    reason="integer string digit limit disabled or unsupported",
)
def test_parse_optional_int_returns_none_for_oversized_integer_string():
    oversized_integer = "1" * (_INT_MAX_STR_DIGITS + 1)

    assert parse_optional_int(oversized_integer) is None


@pytest.mark.skipif(
    _INT_MAX_STR_DIGITS == 0,
    reason="integer string digit limit disabled or unsupported",
)
def test_validate_report_charging_profiles_payload_rejects_oversized_integer_string():
    oversized_integer = "1" * (_INT_MAX_STR_DIGITS + 1)
    payload = _minimum_payload(
        duration=oversized_integer,
        chargingSchedulePeriod=[{"startPeriod": oversized_integer, "limit": 5.5}],
    )
    payload["chargingProfile"]["stackLevel"] = oversized_integer

    with pytest.raises(ReportChargingProfilesValidationError):
        validate_report_charging_profiles_payload(payload, connector_value=None)


@pytest.mark.parametrize(
    "msg",
    [[], [2, "id", "BootNotification"], [2, "id", "BootNotification", {}, "extra"]],
)
def test_validate_call_envelope_rejects_wrong_length(msg):
    assert validate_call_envelope(msg) is None


@pytest.mark.parametrize(
    "msg", [[2, "id", "BootNotification", []], [2, "id", "BootNotification", None]]
)
def test_validate_call_envelope_rejects_non_dict_payload(msg):
    assert validate_call_envelope(msg) is None


def test_validate_call_envelope_accepts_valid_call():
    result = validate_call_envelope(
        [2, "test-id", "BootNotification", {"key": "value"}]
    )

    assert result is not None
    assert result.message_id == "test-id"
    assert result.action == "BootNotification"
    assert result.payload == {"key": "value"}


def test_validate_call_envelope_rejects_non_call_frame():
    assert validate_call_envelope([3, "id", {}]) is None


def test_validate_call_envelope_rejects_non_list_frame():
    assert validate_call_envelope({"messageId": "id"}) is None
