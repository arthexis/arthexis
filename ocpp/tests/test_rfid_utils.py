from __future__ import annotations

from django.test import RequestFactory

from ocpp.rfid import utils


def test_normalize_endianness_accepts_valid_string():
    assert utils.normalize_endianness(" little ") == "LITTLE"


def test_normalize_endianness_falls_back_to_default():
    assert utils.normalize_endianness("unknown") == "BIG"


def test_convert_endianness_value_reorders_bytes_between_modes():
    value = "AA BB CC DD"
    converted = utils.convert_endianness_value(
        value,
        from_endianness="big",
        to_endianness="little",
    )

    assert converted == "DDCCBBAA"


def test_convert_endianness_value_returns_sanitized_when_lengths_odd():
    assert utils.convert_endianness_value("ABC", from_endianness="big", to_endianness="little") == "ABC"


def test_convert_endianness_value_handles_non_string_input():
    assert utils.convert_endianness_value(12345) == ""


def test_build_mode_toggle_generates_table_mode_link():
    request = RequestFactory().get("/rfid/list/", {"query": "abc"})
    table_mode, toggle_url, toggle_label = utils.build_mode_toggle(request)

    assert not table_mode
    assert toggle_url.endswith("mode=table")
    assert str(toggle_label) == "Table Mode"


def test_build_mode_toggle_returns_single_mode_link_for_table_requests():
    request = RequestFactory().get("/rfid/list/", {"mode": "table", "page": "2"})
    table_mode, toggle_url, toggle_label = utils.build_mode_toggle(request, base_path="/rfid/custom/")

    assert table_mode
    assert toggle_url == "/rfid/custom/?page=2"
    assert str(toggle_label) == "Single Mode"
