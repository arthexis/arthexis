from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.cards import reader
from apps.cards.models import RFID


class _FakeReader:
    MI_OK = 0
    MI_ERR = 1
    PICC_REQIDL = 2
    PICC_AUTHENT1A = 3
    PICC_AUTHENT1B = 4

    def __init__(
        self,
        *,
        request_status=0,
        anticoll_status=0,
        uid=None,
        select_result=True,
    ):
        self.request_status = request_status
        self.anticoll_status = anticoll_status
        self.uid = uid or []
        self.select_result = select_result
        self.stop_calls = 0

    def MFRC522_Request(self, _mode):
        return self.request_status, None

    def MFRC522_Anticoll(self):
        return self.anticoll_status, list(self.uid)

    def MFRC522_SelectTag(self, uid):
        self.selected_uid = list(uid)
        return self.select_result

    def MFRC522_StopCrypto1(self):
        self.stop_calls += 1


@pytest.mark.parametrize(
    ("uid_bytes", "expected_rfid", "expected_kind"),
    [
        ([0x0A, 0xBC, 0x01, 0xFF], "0ABC01FF", RFID.CLASSIC),
        ([1, 2, 3, 4, 5, 6, 7], "01020304050607", RFID.NTAG215),
    ],
)
def test_decode_scanned_rfid_normalizes_uid_payload(uid_bytes, expected_rfid, expected_kind):
    payload = reader._decode_scanned_rfid(uid_bytes)

    assert payload == {"uid": uid_bytes, "rfid": expected_rfid, "kind": expected_kind}


def test_initialize_reader_strategy_wraps_provided_reader():
    fake_reader = object()

    strategy, failure = reader._initialize_reader_strategy(fake_reader, cleanup=False)

    assert failure is None
    assert strategy == reader.ReaderStrategy(
        mfrc=fake_reader,
        cleanup_gpio=False,
        source="provided",
    )


def test_read_rfid_returns_initialization_failure(monkeypatch):
    monkeypatch.setattr(
        reader,
        "_initialize_reader_strategy",
        lambda mfrc=None, *, cleanup=True: (None, {"error": "reader unavailable"}),
    )

    result = reader.read_rfid()

    assert result == {"error": "reader unavailable"}


def test_read_rfid_returns_empty_payload_when_polling_times_out(monkeypatch):
    fake_reader = _FakeReader(request_status=_FakeReader.MI_ERR)
    strategy = reader.ReaderStrategy(
        mfrc=fake_reader,
        cleanup_gpio=False,
        source="provided",
    )
    monkeypatch.setattr(
        reader,
        "_initialize_reader_strategy",
        lambda mfrc=None, *, cleanup=True: (strategy, None),
    )
    monkeypatch.setattr(reader, "_finalize_reader_session", lambda *args, **kwargs: None)
    times = iter([100.0, 101.0])
    monkeypatch.setattr(reader.time, "time", lambda: next(times))

    result = reader.read_rfid(timeout=0.5, poll_interval=None)

    assert result == {"rfid": None, "label_id": None}


def test_read_rfid_uses_decoded_uid_payload(monkeypatch):
    fake_reader = _FakeReader(uid=[0xDE, 0xAD, 0xBE, 0xEF])
    basic_result = {"rfid": "DEADBEEF", "label_id": 10}
    strategy = reader.ReaderStrategy(
        mfrc=fake_reader,
        cleanup_gpio=False,
        source="provided",
    )
    monkeypatch.setattr(
        reader,
        "_initialize_reader_strategy",
        lambda mfrc=None, *, cleanup=True: (strategy, None),
    )
    monkeypatch.setattr(reader, "_finalize_reader_session", lambda *args, **kwargs: None)

    captured = {}

    def _fake_read_basic_tag_data(decoded_card):
        captured["decoded_card"] = decoded_card
        return SimpleNamespace(kind=RFID.CLASSIC), False, dict(basic_result)

    monkeypatch.setattr(reader, "_read_basic_tag_data", _fake_read_basic_tag_data)

    result = reader.read_rfid(timeout=0.1, poll_interval=None)

    assert captured["decoded_card"] == {
        "uid": [0xDE, 0xAD, 0xBE, 0xEF],
        "rfid": "DEADBEEF",
        "kind": RFID.CLASSIC,
    }
    assert result == basic_result


def test_read_rfid_returns_error_and_notifies_on_processing_failure(monkeypatch):
    fake_reader = _FakeReader(uid=[0xAA, 0xBB, 0xCC, 0xDD])
    strategy = reader.ReaderStrategy(
        mfrc=fake_reader,
        cleanup_gpio=False,
        source="provided",
    )
    monkeypatch.setattr(
        reader,
        "_initialize_reader_strategy",
        lambda mfrc=None, *, cleanup=True: (strategy, None),
    )
    monkeypatch.setattr(reader, "_finalize_reader_session", lambda *args, **kwargs: None)

    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        reader,
        "notify_async",
        lambda title, body: notifications.append((title, body)),
    )

    def _boom(_decoded_card):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(reader, "_read_basic_tag_data", _boom)

    result = reader.read_rfid(timeout=0.1, poll_interval=None)

    assert result == {"error": "decode failed"}
    assert notifications == [("RFID AABBCCDD", "Read failed")]
