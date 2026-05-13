from __future__ import annotations

import pytest

from apps.cards import classic_layout


def _dump_from_blocks(blocks):
    return [{"block": block, "data": data} for block, data in blocks.items()]


def test_lcd_label_round_trips_as_two_16_byte_lines():
    encoded = classic_layout.encode_lcd_label("Front Desk Ready\nScan OK")

    assert len(encoded) == 32
    assert classic_layout.decode_lcd_label(encoded) == "Front Desk Ready\nScan OK"


def test_trait_record_uses_sector_pair_for_80_byte_value():
    value = "V" * classic_layout.TRAIT_VALUE_BYTES
    blocks = classic_layout.build_trait_block_payloads(3, "door", value)

    assert sorted(blocks) == [12, 13, 14, 16, 17, 18]

    traits = classic_layout.decode_traits_from_dump(_dump_from_blocks(blocks))

    assert traits == {
        "door": {
            "value": value,
            "sector": 3,
            "sectors": [3, 4],
        }
    }


def test_trait_key_and_value_enforce_ascii_capacity():
    with pytest.raises(classic_layout.CardLayoutError):
        classic_layout.normalize_trait_key("x" * 17)

    with pytest.raises(classic_layout.CardLayoutError):
        classic_layout.normalize_trait_value("x" * 81)


def test_first_empty_trait_sector_skips_used_sector_pair():
    records = {
        "door": {
            "value": "open",
            "sector": 3,
            "sectors": [3, 4],
        }
    }

    assert classic_layout.first_empty_trait_sector(records) == 5


def test_trait_sigils_export_safe_environment_names():
    records = {
        "door mode": {
            "value": "open",
            "sector": 3,
            "sectors": [3, 4],
        }
    }

    assert classic_layout.trait_sigils(records) == {"SIGIL_DOOR_MODE": "open"}


def test_transport_metadata_decodes_lcd_label_and_writer_blocks():
    blocks = {
        classic_layout.sector_block(0, 1): classic_layout.encode_lcd_label("Line 1\nLine 2")[:16],
        classic_layout.sector_block(0, 2): classic_layout.encode_lcd_label("Line 1\nLine 2")[16:],
        classic_layout.sector_block(1, 1): classic_layout.encode_writer_id("MODEL-1"),
        classic_layout.sector_block(1, 2): list(b"20260513T123456Z"),
    }

    metadata = classic_layout.decode_transport_metadata(_dump_from_blocks(blocks))

    assert metadata["lcd_label"] == "Line 1\nLine 2"
    assert metadata["writer"] == {
        "id": "MODEL-1",
        "written_at": "20260513T123456Z",
    }


def test_classic_1k_layout_stays_inside_standard_sector_range():
    assert classic_layout.LAST_MANAGED_SECTOR == 15
    assert list(classic_layout.sector_numbers()) == list(range(16))
    assert classic_layout.scan_block_count() == 64
    assert classic_layout.trait_sector_pairs()[-1] == (13, 14)
