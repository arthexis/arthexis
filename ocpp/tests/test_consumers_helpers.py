from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

import pytest

from ocpp.consumers import (
    CSMSConsumer,
    SERIAL_QUERY_PARAM_NAMES,
    _resolve_client_ip,
)
from ocpp import store
from ocpp.models import Charger
from core.models import RFID
from asgiref.sync import async_to_sync


@pytest.fixture(autouse=True)
def _clear_store_state():
    store.pending_calls.clear()
    yield
    store.pending_calls.clear()


@pytest.mark.parametrize(
    "scope, expected",
    [
        (
            {
                "headers": [
                    (b"forwarded", b'for="[2001:db8::1]:8443"'),
                ],
                "client": ("192.0.2.1", 12345),
            },
            "2001:db8::1",
        ),
        (
            {
                "headers": [
                    (b"x-real-ip", b"'203.0.113.5:9000'"),
                ],
                "client": ("198.51.100.2", 54321),
            },
            "203.0.113.5",
        ),
        (
            {
                "headers": [
                    (
                        b"forwarded",
                        b"for=unknown, for=\"[2001:db8::2]:443\", for=192.0.2.7",
                    ),
                ],
                "client": ("198.51.100.3", 1111),
            },
            "2001:db8::2",
        ),
        (
            {
                "headers": [],
                "client": ("198.51.100.9", 4242),
            },
            "198.51.100.9",
        ),
    ],
)
def test_resolve_client_ip(scope, expected):
    assert _resolve_client_ip(scope) == expected



def test_resolve_client_ip_with_loopback_candidates():
    scope = {
        "headers": [
            (b"x-forwarded-for", b"127.0.0.1, ::1"),
        ],
        "client": ("127.0.0.2", 5000),
    }

    assert _resolve_client_ip(scope) == "127.0.0.1"


@pytest.mark.parametrize(
    "header, expected",
    [
        ((b"x-forwarded-for", b"[2001:db8::5]:8080"), "2001:db8::5"),
        ((b"x-real-ip", b"\"192.0.2.55:443\""), "192.0.2.55"),
        ((b"forwarded", b"for='203.0.113.77:8443'"), "203.0.113.77"),
    ],
)
def test_resolve_client_ip_trims_formats(header, expected):
    scope = {"headers": [header]}

    assert _resolve_client_ip(scope) == expected


@pytest.mark.parametrize("query_key", SERIAL_QUERY_PARAM_NAMES)
def test_extract_serial_identifier_from_query_params(query_key):
    consumer = CSMSConsumer()
    consumer.scope = {
        "query_string": f"{query_key}= %20SER123%20".encode(),
        "url_route": {"kwargs": {"cid": "FALLBACK"}},
    }

    assert consumer._extract_serial_identifier() == "SER123"


def test_extract_serial_identifier_falls_back_to_url_route():
    consumer = CSMSConsumer()
    consumer.scope = {
        "query_string": b"",
        "url_route": {"kwargs": {"cid": "FALLBACK"}},
    }

    assert consumer._extract_serial_identifier() == "FALLBACK"


def test_ensure_rfid_seen_marks_allowed_and_released():
    charger = Charger.objects.create(charger_id="CPRFID")
    consumer = CSMSConsumer()
    consumer.charger_id = "CPRFID"
    consumer.connector_value = None
    consumer.charger = charger
    consumer.aggregate_charger = charger

    tag = async_to_sync(consumer._ensure_rfid_seen)("feedface")

    tag.refresh_from_db()
    assert tag.allowed is True
    assert tag.released is True


def test_handle_send_local_list_result_updates_version():
    charger = Charger.objects.create(charger_id="CP001")
    consumer = CSMSConsumer()
    consumer.charger_id = "CP001"
    consumer.connector_value = None
    consumer.store_key = store.identity_key("CP001", None)
    consumer.charger = charger
    consumer.aggregate_charger = charger

    store.register_pending_call(
        "msg-send",
        {
            "action": "SendLocalList",
            "charger_id": "CP001",
            "log_key": consumer.store_key,
            "list_version": 4,
        },
    )

    async_to_sync(consumer._handle_call_result)(
        "msg-send",
        {"status": "Accepted"},
    )

    charger.refresh_from_db()
    assert charger.local_auth_list_version == 4
    assert charger.local_auth_list_updated_at is not None


def test_handle_get_local_list_version_applies_entries():
    charger = Charger.objects.create(charger_id="CP002")
    consumer = CSMSConsumer()
    consumer.charger_id = "CP002"
    consumer.connector_value = None
    consumer.store_key = store.identity_key("CP002", None)
    consumer.charger = charger
    consumer.aggregate_charger = charger

    store.register_pending_call(
        "msg-get",
        {
            "action": "GetLocalListVersion",
            "charger_id": "CP002",
            "log_key": consumer.store_key,
        },
    )

    payload = {
        "listVersion": 7,
        "localAuthorizationList": [
            {"idTag": "ABC123", "idTagInfo": {"status": "Accepted"}},
            {"idTag": "DEF456", "idTagInfo": {"status": "Blocked"}},
        ],
    }

    async_to_sync(consumer._handle_call_result)("msg-get", payload)

    charger.refresh_from_db()
    assert charger.local_auth_list_version == 7
    assert charger.local_auth_list_updated_at is not None

    accepted = RFID.objects.get(rfid="ABC123")
    blocked = RFID.objects.get(rfid="DEF456")

    assert accepted.allowed is True
    assert accepted.released is True
    assert blocked.allowed is False
    assert blocked.released is False
