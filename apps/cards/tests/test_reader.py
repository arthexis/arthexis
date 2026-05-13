from __future__ import annotations

import sys
from datetime import datetime
from datetime import timezone as datetime_timezone
from types import ModuleType, SimpleNamespace

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
        read_blocks=None,
    ):
        self.request_status = request_status
        self.anticoll_status = anticoll_status
        self.uid = uid or []
        self.select_result = select_result
        self.read_blocks = read_blocks or {}
        self.stop_calls = 0

    def MFRC522_Request(self, _mode):
        return self.request_status, None

    def MFRC522_Anticoll(self):
        return self.anticoll_status, list(self.uid)

    def MFRC522_SelectTag(self, uid):
        self.selected_uid = list(uid)
        return self.select_result

    def MFRC522_Auth(self, _auth_mode, block, _key_bytes, _uid):
        return self.MI_OK if block in self.read_blocks else self.MI_ERR

    def MFRC522_Read(self, block):
        data = self.read_blocks.get(block)
        if data is None:
            return self.MI_ERR, None
        return self.MI_OK, list(data)

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


def test_default_reader_strategy_suppresses_gpio_warnings(monkeypatch):
    events: list[tuple[str, object]] = []

    gpio_module = ModuleType("RPi.GPIO")

    def _setwarnings(value):
        events.append(("setwarnings", value))

    gpio_module.setwarnings = _setwarnings

    rpi_module = ModuleType("RPi")
    rpi_module.GPIO = gpio_module

    class _DefaultReader:
        def __init__(self, **kwargs):
            events.append(("reader", kwargs))

    mfrc522_module = ModuleType("mfrc522")
    mfrc522_module.MFRC522 = _DefaultReader

    monkeypatch.setitem(sys.modules, "RPi", rpi_module)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio_module)
    monkeypatch.setitem(sys.modules, "mfrc522", mfrc522_module)
    monkeypatch.setattr(reader, "resolve_spi_bus_device", lambda: (0, 0))

    strategy, failure = reader._init_default_reader_strategy(cleanup=False)

    assert failure is None
    assert strategy is not None
    assert strategy.source == "default"
    assert events[0] == ("setwarnings", False)
    assert events[1] == (
        "reader",
        {
            "bus": 0,
            "device": 0,
            "pin_mode": reader.GPIO_PIN_MODE_BCM,
            "pin_rst": reader.DEFAULT_RST_PIN,
        },
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


def test_read_rfid_adds_transport_lcd_label(monkeypatch):
    encoded = reader.encode_lcd_label("Door Ready\nTap")
    fake_reader = _FakeReader(
        uid=[0xDE, 0xAD, 0xBE, 0xEF],
        read_blocks={
            reader.sector_block(0, 1): encoded[:16],
            reader.sector_block(0, 2): encoded[16:],
            reader.sector_block(1, 1): [0] * 16,
            reader.sector_block(1, 2): [0] * 16,
        },
    )
    tag = SimpleNamespace(kind=RFID.CLASSIC, lcd_label="", traits={})
    tag.save = lambda *, update_fields: None
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
    monkeypatch.setattr(
        reader,
        "_read_basic_tag_data",
        lambda decoded_card: (tag, False, {"rfid": "DEADBEEF", "label_id": 10}),
    )

    result = reader.read_rfid(timeout=0.1, poll_interval=None)

    assert result["lcd_label"] == "Door Ready\nTap"


def test_read_rfid_clears_blank_transport_metadata(monkeypatch):
    fake_reader = _FakeReader(
        uid=[0xDE, 0xAD, 0xBE, 0xEF],
        read_blocks={
            reader.sector_block(0, 1): [0] * 16,
            reader.sector_block(0, 2): [0] * 16,
            reader.sector_block(1, 1): [0] * 16,
            reader.sector_block(1, 2): [0] * 16,
        },
    )
    tag = SimpleNamespace(
        kind=RFID.CLASSIC,
        lcd_label="Old label",
        writer_id="OLD-WRITER",
        writer_written_at=datetime(2026, 5, 13, tzinfo=datetime_timezone.utc),
        traits={},
    )
    saved_fields = []
    tag.save = lambda *, update_fields: saved_fields.append(list(update_fields))
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
    monkeypatch.setattr(
        reader,
        "_read_basic_tag_data",
        lambda decoded_card: (
            tag,
            False,
            {
                "rfid": "DEADBEEF",
                "label_id": 10,
                "lcd_label": "Old label",
                "writer": {"id": "OLD-WRITER"},
            },
        ),
    )

    result = reader.read_rfid(timeout=0.1, poll_interval=None)

    assert result["lcd_label"] == ""
    assert "writer" not in result
    assert tag.lcd_label == ""
    assert tag.writer_id == ""
    assert tag.writer_written_at is None
    assert saved_fields == [["lcd_label", "writer_id", "writer_written_at"]]


def test_read_rfid_ignores_partial_blank_transport_metadata(monkeypatch):
    fake_reader = _FakeReader(
        uid=[0xDE, 0xAD, 0xBE, 0xEF],
        read_blocks={
            reader.sector_block(0, 1): [0] * 16,
        },
    )
    tag = SimpleNamespace(
        kind=RFID.CLASSIC,
        lcd_label="Old label",
        writer_id="OLD-WRITER",
        writer_written_at=datetime(2026, 5, 13, tzinfo=datetime_timezone.utc),
        traits={},
    )
    saved_fields = []
    tag.save = lambda *, update_fields: saved_fields.append(list(update_fields))
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
    monkeypatch.setattr(
        reader,
        "_read_basic_tag_data",
        lambda decoded_card: (
            tag,
            False,
            {"rfid": "DEADBEEF", "label_id": 10, "lcd_label": "Old label"},
        ),
    )

    result = reader.read_rfid(timeout=0.1, poll_interval=None)

    assert result["lcd_label"] == "Old label"
    assert tag.lcd_label == "Old label"
    assert tag.writer_id == "OLD-WRITER"
    assert tag.writer_written_at == datetime(2026, 5, 13, tzinfo=datetime_timezone.utc)
    assert saved_fields == []


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


def test_read_deep_classic_tag_data_logs_and_skips_block_errors(monkeypatch):
    class _DeepReadReader:
        MI_OK = 0
        MI_ERR = 1
        PICC_AUTHENT1A = 3
        PICC_AUTHENT1B = 4

        def MFRC522_Auth(self, _auth_mode, block, _key_bytes, _uid):
            return self.MI_OK if block == 0 else self.MI_ERR

        def MFRC522_Read(self, block):
            if block == 0:
                raise RuntimeError("read failed")
            return self.MI_ERR, None

    tag = SimpleNamespace(
        key_a="FFFFFFFFFFFF",
        key_a_verified=False,
        key_b="",
        key_b_verified=False,
        data=None,
    )
    saves: list[list[str]] = []
    tag.save = lambda *, update_fields: saves.append(list(update_fields))

    debug_messages: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        reader.logger,
        "debug",
        lambda message, block, exc: debug_messages.append((message, block, str(exc))),
    )

    result = reader._read_deep_classic_tag_data(
        _DeepReadReader(),
        tag,
        [1, 2, 3, 4],
        {"rfid": "01020304"},
    )

    assert result["dump"] == []
    assert saves == [["data"]]
    assert debug_messages == [
        ("Failed to read block %d for classic tag: %s", 0, "read failed")
    ]


def test_initialize_detected_card_requires_all_managed_sectors(monkeypatch):
    tag = SimpleNamespace(
        pk=17,
        sector_keys={},
        initialized_on=None,
        traits={"door": {"value": "open", "sector": 3, "sectors": [3, 4]}},
    )
    saved_fields = []
    tag.save = lambda *, update_fields: saved_fields.append(list(update_fields))

    monkeypatch.setattr(reader, "_write_writer_metadata", lambda *args, **kwargs: ({}, set()))
    monkeypatch.setattr(reader, "managed_sector_numbers", lambda: [3, 4])
    monkeypatch.setattr(reader, "sector_data_blocks", lambda sector: [sector * 4])
    monkeypatch.setattr(reader, "sector_trailer_block", lambda sector: (sector * 4) + 3)
    monkeypatch.setattr(reader, "build_sector_trailer", lambda _a, _b: [0] * 16)

    def _write_block(_mfrc, _tag, _uid, block, _data):
        return block != 19, "FFFFFFFFFFFF"

    monkeypatch.setattr(reader, "_write_block_with_candidates", _write_block)

    result = reader._initialize_detected_card(object(), [1, 2, 3, 4], "01020304", tag=tag)

    assert result["initialized"] is False
    assert result["initialized_sectors"] == [3]
    assert result["errors"] == [{"sector": 4, "errors": ["trailer 19"]}]
    assert tag.initialized_on is None
    assert tag.traits == {}
    assert "initialized_on" not in saved_fields[0]
    assert saved_fields == [["sector_keys", "traits"]]


def test_set_current_card_trait_aborts_when_auto_initialization_fails(monkeypatch):
    tag = SimpleNamespace(initialized_on=None, traits={})
    refreshed = []

    def _refresh_from_db():
        refreshed.append(True)

    tag.refresh_from_db = _refresh_from_db
    monkeypatch.setattr(reader.RFID, "register_scan", lambda rfid, kind: (tag, False))
    monkeypatch.setattr(
        reader,
        "_initialize_detected_card",
        lambda *args, **kwargs: {
            "initialized": False,
            "errors": [{"sector": 4, "errors": ["trailer 19"]}],
        },
    )
    monkeypatch.setattr(
        reader,
        "_with_detected_rfid_card",
        lambda timeout, callback: callback(object(), [1, 2, 3, 4], "01020304"),
    )
    monkeypatch.setattr(
        reader,
        "_write_writer_metadata",
        lambda *args, **kwargs: pytest.fail("trait write should not continue"),
    )

    result = reader.set_current_card_trait(key="door", value="open")

    assert result["error"] == "Unable to initialize RFID card before writing trait"
    assert result["initialization"]["errors"] == [
        {"sector": 4, "errors": ["trailer 19"]}
    ]
    assert refreshed == []


def test_save_tag_traits_from_dump_clears_stale_traits_when_none_decoded():
    tag = SimpleNamespace(traits={"door": {"value": "open"}})
    saved_fields = []
    tag.save = lambda *, update_fields: saved_fields.append(list(update_fields))
    result = {"traits": {"door": "open"}, "trait_sigils": {"SIGIL_DOOR": "open"}}

    reader._save_tag_traits_from_dump(tag, [], result)

    assert tag.traits == {}
    assert saved_fields == [["traits"]]
    assert "traits" not in result
    assert "trait_sigils" not in result


def test_deep_classic_read_reuses_and_promotes_sector_key_candidates(monkeypatch):
    good_key = [1, 2, 3, 4, 5, 6]
    bad_key = [6, 5, 4, 3, 2, 1]

    class _DeepReadReader:
        MI_OK = 0
        MI_ERR = 1
        PICC_AUTHENT1A = 3
        PICC_AUTHENT1B = 4

        def __init__(self):
            self.attempts = []

        def MFRC522_Auth(self, auth_mode, block, key_bytes, _uid):
            self.attempts.append((auth_mode, block, list(key_bytes)))
            return self.MI_OK if list(key_bytes) == good_key else self.MI_ERR

        def MFRC522_Read(self, block):
            return self.MI_OK, [block] * 16

    build_calls = []

    def _build_candidates(_tag, sector, key_type):
        build_calls.append((sector, key_type))
        if key_type == "A":
            return [("BADBADBADBAD", bad_key), ("010203040506", good_key)]
        return []

    monkeypatch.setattr(reader, "scan_block_count", lambda: 2)
    monkeypatch.setattr(reader, "_build_sector_key_candidates", _build_candidates)

    tag = SimpleNamespace(
        key_a="",
        key_a_verified=False,
        key_b="",
        key_b_verified=False,
        data=None,
        sector_keys={},
        traits={},
    )
    saves: list[list[str]] = []
    tag.save = lambda *, update_fields: saves.append(list(update_fields))
    deep_reader = _DeepReadReader()

    result = reader._read_deep_classic_tag_data(
        deep_reader,
        tag,
        [1, 2, 3, 4],
        {"rfid": "01020304"},
    )

    assert build_calls == [(0, "A"), (0, "B")]
    assert deep_reader.attempts == [
        (_DeepReadReader.PICC_AUTHENT1A, 0, bad_key),
        (_DeepReadReader.PICC_AUTHENT1A, 0, good_key),
        (_DeepReadReader.PICC_AUTHENT1A, 1, good_key),
    ]
    assert [entry["block"] for entry in result["dump"]] == [0, 1]
