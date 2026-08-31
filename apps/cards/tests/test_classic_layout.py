from __future__ import annotations

import pytest

from apps.cards import classic_layout, command_layout


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


def test_transport_metadata_decodes_command_card_name_and_metadata_block():
    metadata_block = command_layout.encode_command_metadata(command_block_count=2)
    blocks = {
        classic_layout.sector_block(0, 1): classic_layout.encode_card_name("Suite Upgrade"),
        classic_layout.sector_block(0, 2): metadata_block,
    }

    metadata = classic_layout.decode_transport_metadata(_dump_from_blocks(blocks))

    assert metadata["card_name"] == "Suite Upgrade"
    assert metadata["lcd_label"] == "Suite Upgrade"
    assert metadata["command_metadata_block"] == metadata_block


def test_transport_metadata_decodes_legacy_two_line_lcd_label():
    encoded = classic_layout.encode_lcd_label("Front Desk\nScan OK")
    blocks = {
        classic_layout.sector_block(0, 1): encoded[:16],
        classic_layout.sector_block(0, 2): encoded[16:32],
    }

    metadata = classic_layout.decode_transport_metadata(_dump_from_blocks(blocks))

    assert metadata["card_name"] == "Front Desk"
    assert metadata["lcd_label"] == "Front Desk\nScan OK"
    assert metadata["command_metadata_block"] == encoded[16:32]
    assert "writer" not in metadata


def test_transport_metadata_decodes_writer_blocks():
    written_at = classic_layout.utc_now()
    blocks = {
        classic_layout.sector_block(0, 1): classic_layout.encode_card_name("Door"),
        classic_layout.sector_block(1, 1): classic_layout.encode_writer_id("NODE-1"),
        classic_layout.sector_block(1, 2): classic_layout.encode_writer_date(written_at),
    }

    metadata = classic_layout.decode_transport_metadata(_dump_from_blocks(blocks))

    assert metadata["writer"] == {
        "id": "NODE-1",
        "written_at": written_at.strftime("%Y%m%dT%H%M%SZ"),
    }


def test_command_layout_round_trips_command_and_result_blocks():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="LOG",
        params={"channel": "stable"},
        sigils={"SIGIL_MODE": "safe"},
        provenance_key="AABBCCDDEEFF0011",
    )
    started_result = {
        "execution_id": "abc",
        "status": "started",
        "ok": True,
        "summary": "Command accepted",
    }
    encoded_result, digest, stored_result = command_layout.encode_result_payload(
        started_result,
        command_block_count=metadata.command_block_count,
    )
    for index, block in enumerate(command_layout.result_data_blocks(metadata.command_block_count)):
        blocks[block] = encoded_result[index * 16 : index * 16 + 16]

    card = command_layout.decode_command_card_from_dump(_dump_from_blocks(blocks))

    assert card is not None
    assert card.name == "Suite Upgrade"
    assert card.command == "LOG"
    assert card.params == {"channel": "stable"}
    assert card.sigils == {"SIGIL_MODE": "safe"}
    assert card.metadata.provenance_key == "AABBCCDDEEFF0011"
    assert card.metadata.lifecycle_mode == command_layout.COMMAND_LIFECYCLE_TRIGGERED
    assert card.metadata.as_dict()["lifecycle_mode"] == (
        command_layout.COMMAND_LIFECYCLE_TRIGGERED
    )
    assert card.result == stored_result
    assert command_layout.result_digest(card.result) == digest


def test_command_payload_blocks_complete_for_single_block_command():
    metadata_block = command_layout.encode_command_metadata(
        command_block_count=1,
        result_block_count=1,
    )
    command_payload = command_layout.canonical_json_bytes({"command": "X"})
    blocks = {
        command_layout.COMMAND_CARD_NAME_BLOCK: classic_layout.encode_card_name(
            "Short"
        ),
        command_layout.COMMAND_METADATA_BLOCK: metadata_block,
        command_layout.command_data_blocks()[0]: list(
            command_payload.ljust(classic_layout.BLOCK_SIZE, b"\x00")
        ),
    }
    metadata = command_layout.decode_command_metadata(metadata_block)

    assert metadata.command_block_count == 1
    assert command_layout.command_payload_blocks_complete(
        _dump_from_blocks(blocks)
    )
    assert not command_layout.command_result_blocks_complete(
        _dump_from_blocks(blocks)
    )


def test_command_payload_blocks_complete_for_multi_block_command_with_extra_blocks():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="LOG",
        params={"message": "x" * 80},
    )
    command_blocks = command_layout.command_data_blocks()[
        : metadata.command_block_count
    ]
    extra_block = command_layout.command_data_blocks()[metadata.command_block_count]
    minimal_blocks = {
        command_layout.COMMAND_CARD_NAME_BLOCK: blocks[
            command_layout.COMMAND_CARD_NAME_BLOCK
        ],
        command_layout.COMMAND_METADATA_BLOCK: blocks[
            command_layout.COMMAND_METADATA_BLOCK
        ],
        **{block: blocks[block] for block in command_blocks},
        extra_block: blocks[extra_block],
    }

    assert metadata.command_block_count > 1
    assert command_layout.command_payload_blocks_complete(
        _dump_from_blocks(minimal_blocks)
    )


def test_command_payload_blocks_complete_rejects_incomplete_and_malformed_cards():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="LOG",
        params={"message": "x" * 80},
    )
    command_blocks = command_layout.command_data_blocks()[
        : metadata.command_block_count
    ]
    incomplete_blocks = {
        command_layout.COMMAND_CARD_NAME_BLOCK: blocks[
            command_layout.COMMAND_CARD_NAME_BLOCK
        ],
        command_layout.COMMAND_METADATA_BLOCK: blocks[
            command_layout.COMMAND_METADATA_BLOCK
        ],
        **{block: blocks[block] for block in command_blocks[:-1]},
    }
    malformed_blocks = dict(blocks)
    malformed_blocks[command_layout.COMMAND_METADATA_BLOCK] = [0] * 16

    assert not command_layout.command_payload_blocks_complete(
        _dump_from_blocks(incomplete_blocks)
    )
    assert not command_layout.command_payload_blocks_complete(
        _dump_from_blocks(malformed_blocks)
    )


def test_command_result_blocks_complete_requires_declared_result_blocks():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="LOG",
        params={},
    )
    result_blocks = command_layout.result_data_blocks(metadata.command_block_count)
    partial_blocks = dict(blocks)
    partial_blocks.pop(result_blocks[-1])

    assert command_layout.command_result_blocks_complete(_dump_from_blocks(blocks))
    assert not command_layout.command_result_blocks_complete(
        _dump_from_blocks(partial_blocks)
    )


def test_command_layout_round_trips_reader_held_lifecycle():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="Hold Effect",
        command="LOG",
        params={"channel": "held"},
        lifecycle_mode=command_layout.COMMAND_LIFECYCLE_READER_HELD,
    )

    card = command_layout.decode_command_card_from_dump(_dump_from_blocks(blocks))

    assert metadata.lifecycle_mode == command_layout.COMMAND_LIFECYCLE_READER_HELD
    assert metadata.flags & command_layout.COMMAND_METADATA_READER_HELD_FLAG
    assert card is not None
    assert card.metadata.lifecycle_mode == command_layout.COMMAND_LIFECYCLE_READER_HELD
    assert card.as_dict()["metadata"]["lifecycle_mode"] == (
        command_layout.COMMAND_LIFECYCLE_READER_HELD
    )


def test_command_payload_digest_tracks_card_name_and_raw_payload():
    blocks, _metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="LOG",
        params={"channel": "stable"},
        provenance_key="AABBCCDDEEFF0011",
    )
    card = command_layout.decode_command_card_from_dump(_dump_from_blocks(blocks))

    assert card is not None
    expected = command_layout.command_payload_digest(
        name="Suite Upgrade",
        command="LOG",
        params={"channel": "stable"},
    )
    assert command_layout.command_payload_digest_for_card(card) == expected

    mutated_blocks, _metadata = command_layout.build_command_card_blocks(
        name="Suite Upgrade",
        command="NOOP",
        params={"channel": "stable"},
        provenance_key="AABBCCDDEEFF0011",
    )
    mutated_card = command_layout.decode_command_card_from_dump(
        _dump_from_blocks(mutated_blocks)
    )

    assert mutated_card is not None
    assert command_layout.command_payload_digest_for_card(mutated_card) != expected


def test_command_metadata_rejects_oversized_result_count():
    metadata_block = command_layout.encode_command_metadata(command_block_count=1)
    metadata_block[6] = len(command_layout.command_data_blocks())

    metadata = command_layout.decode_command_metadata(metadata_block)

    assert metadata.valid is False


def test_command_metadata_tolerates_invalid_bytes():
    metadata = command_layout.decode_command_metadata([65, 88, 67, 49, 1, "bad"])

    assert metadata.valid is False


def test_command_payload_requires_json_object_params():
    with pytest.raises(classic_layout.CardLayoutError):
        command_layout.encode_command_payload(command="LOG", params=["not", "object"])

    with pytest.raises(classic_layout.CardLayoutError):
        command_layout.encode_command_payload(command="LOG", sigils=["not", "object"])


def test_command_payload_starts_in_managed_sectors_and_reserves_result_space():
    blocks = command_layout.command_data_blocks()
    assert blocks[0] == classic_layout.sector_block(classic_layout.FIRST_MANAGED_SECTOR, 0)

    capacity = (
        len(blocks) - command_layout.MIN_RESULT_BLOCKS
    ) * classic_layout.BLOCK_SIZE
    padding = ""
    for length in range(capacity):
        candidate = "x" * length
        encoded = command_layout.canonical_json_bytes(
            {"command": "LOG", "params": {"blob": candidate}}
        )
        if len(encoded) == capacity:
            padding = candidate
            break
    assert padding

    _blocks, metadata = command_layout.build_command_card_blocks(
        name="Max Command",
        command="LOG",
        params={"blob": padding},
    )

    assert metadata.result_block_count >= command_layout.MIN_RESULT_BLOCKS
    encoded, _digest, _stored = command_layout.encode_result_payload(
        {"status": "started"},
        command_block_count=metadata.command_block_count,
    )
    assert encoded
    encoded, _digest, stored = command_layout.encode_result_payload(
        {
            "execution_id": "x" * 64,
            "status": "succeeded",
            "ok": True,
            "command": "LOG",
            "triggered_at": "2026-05-31T00:00:00+00:00",
            "reader": "reader-1",
            "summary": "x" * 200,
            "payload": {"blob": "x" * 1000},
        },
        command_block_count=metadata.command_block_count,
    )
    assert stored["truncated"] is True
    assert len(encoded) == metadata.result_block_count * classic_layout.BLOCK_SIZE
    assert len(command_layout.canonical_json_bytes(stored)) <= len(encoded)
    with pytest.raises(classic_layout.CardLayoutError):
        command_layout.build_command_card_blocks(
            name="Too Large",
            command="LOG",
            params={"blob": padding + "x"},
        )


def test_card_name_normalization_matches_decode_trimming():
    encoded = classic_layout.encode_card_name("Suite Upgrade ")

    assert classic_layout.decode_card_name(encoded) == "Suite Upgrade"


def test_classic_1k_layout_stays_inside_standard_sector_range():
    assert classic_layout.LAST_MANAGED_SECTOR == 15
    assert list(classic_layout.sector_numbers()) == list(range(16))
    assert classic_layout.scan_block_count() == 64
    assert classic_layout.trait_sector_pairs()[-1] == (13, 14)
