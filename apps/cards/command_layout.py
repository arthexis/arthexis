from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from apps.cards.classic_layout import (
    BLOCK_SIZE,
    COMMAND_METADATA_BLOCK_OFFSET,
    COMMAND_METADATA_BYTES,
    COMMAND_METADATA_SECTOR,
    FIRST_MANAGED_SECTOR,
    LAST_MANAGED_SECTOR,
    CardLayoutError,
    encode_card_name,
    normalize_card_name,
    sector_block,
    sector_data_blocks,
    zero_block,
)

COMMAND_LAYOUT_MAGIC = b"AXC1"
COMMAND_LAYOUT_VERSION = 1
COMMAND_CARD_NAME_BLOCK = sector_block(0, 1)
COMMAND_METADATA_BLOCK = sector_block(
    COMMAND_METADATA_SECTOR,
    COMMAND_METADATA_BLOCK_OFFSET,
)
# Command/result payloads live in the managed range; sectors below this are
# reserved for manufacturer/header compatibility and legacy transport metadata.
COMMAND_DATA_START_SECTOR = FIRST_MANAGED_SECTOR
COMMAND_PAYLOAD_ENCODING = "utf-8"
RESULT_DIGEST_ALGORITHM = "sha256"
# The truncated result envelope includes status, ok, digest, ref, summary, and
# a truncation marker; 19 blocks gives it 304 bytes of card storage.
MIN_RESULT_BLOCKS = 19
COMMAND_LIFECYCLE_TRIGGERED = "triggered"
COMMAND_LIFECYCLE_READER_HELD = "reader_held"
COMMAND_LIFECYCLE_MODES = {
    COMMAND_LIFECYCLE_TRIGGERED,
    COMMAND_LIFECYCLE_READER_HELD,
}
COMMAND_METADATA_READER_HELD_FLAG = 0x01


@dataclass(frozen=True)
class CommandCardMetadata:
    version: int
    command_block_count: int
    result_block_count: int
    flags: int
    provenance_key: str
    valid: bool = True

    @property
    def lifecycle_mode(self) -> str:
        return lifecycle_mode_from_flags(self.flags)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "command_block_count": self.command_block_count,
            "result_block_count": self.result_block_count,
            "flags": self.flags,
            "lifecycle_mode": self.lifecycle_mode,
            "provenance_key": self.provenance_key,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class DecodedCommandCard:
    name: str
    metadata: CommandCardMetadata
    command: str
    params: dict[str, Any]
    sigils: dict[str, Any]
    raw_command: dict[str, Any]
    result: dict[str, Any]

    @property
    def command_block_count(self) -> int:
        return self.metadata.command_block_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metadata": self.metadata.as_dict(),
            "command": self.command,
            "params": self.params,
            "sigils": self.sigils,
            "result": self.result,
        }


def command_data_blocks() -> list[int]:
    blocks: list[int] = []
    for sector in range(COMMAND_DATA_START_SECTOR, LAST_MANAGED_SECTOR + 1):
        blocks.extend(sector_data_blocks(sector))
    return blocks


def provenance_key_for_reader(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16].upper()


def normalize_command_lifecycle_mode(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"held", "reader", "reader_held", "readerheld"}:
        return COMMAND_LIFECYCLE_READER_HELD
    if normalized in {"", "trigger", "triggered"}:
        return COMMAND_LIFECYCLE_TRIGGERED
    raise CardLayoutError("command lifecycle mode is invalid")


def flags_for_lifecycle_mode(value: object) -> int:
    mode = normalize_command_lifecycle_mode(value)
    if mode == COMMAND_LIFECYCLE_READER_HELD:
        return COMMAND_METADATA_READER_HELD_FLAG
    return 0


def lifecycle_mode_from_flags(flags: int) -> str:
    if int(flags or 0) & COMMAND_METADATA_READER_HELD_FLAG:
        return COMMAND_LIFECYCLE_READER_HELD
    return COMMAND_LIFECYCLE_TRIGGERED


def _normalize_provenance_key(value: object) -> str:
    text = "".join(str(value or "").split()).upper()
    if not text:
        return ""
    if len(text) == 16 and all(ch in "0123456789ABCDEF" for ch in text):
        return text
    return provenance_key_for_reader(text)


def encode_command_metadata(
    *,
    command_block_count: int,
    result_block_count: int = 0,
    flags: int = 0,
    provenance_key: str = "",
    version: int = COMMAND_LAYOUT_VERSION,
) -> list[int]:
    available_blocks = len(command_data_blocks())
    if command_block_count <= 0:
        raise CardLayoutError("command payload must use at least one block")
    if command_block_count > available_blocks:
        raise CardLayoutError("command payload does not fit on this card")
    if result_block_count < 0:
        raise CardLayoutError("result block count must be non-negative")
    if result_block_count and command_block_count + result_block_count > available_blocks:
        raise CardLayoutError("result payload does not fit on this card")

    normalized_provenance = _normalize_provenance_key(provenance_key)
    provenance_bytes = (
        bytes.fromhex(normalized_provenance)
        if normalized_provenance
        else b"\x00" * 8
    )
    payload = bytearray(COMMAND_METADATA_BYTES)
    payload[0:4] = COMMAND_LAYOUT_MAGIC
    payload[4] = int(version) & 0xFF
    payload[5] = int(command_block_count) & 0xFF
    payload[6] = int(result_block_count) & 0xFF
    payload[7] = int(flags) & 0xFF
    payload[8:16] = provenance_bytes[:8].ljust(8, b"\x00")
    return list(payload)


def decode_command_metadata(data: object) -> CommandCardMetadata:
    if not isinstance(data, (bytes, bytearray, list, tuple)):
        return CommandCardMetadata(0, 0, 0, 0, "", valid=False)
    values: list[int] = []
    for value in list(data)[:COMMAND_METADATA_BYTES]:
        try:
            values.append(int(value) & 0xFF)
        except (TypeError, ValueError):
            values.append(0)
    raw = bytes(values)
    raw = raw.ljust(COMMAND_METADATA_BYTES, b"\x00")
    if raw[:4] != COMMAND_LAYOUT_MAGIC:
        return CommandCardMetadata(0, 0, 0, 0, "", valid=False)
    command_blocks = int(raw[5])
    result_blocks = int(raw[6])
    available_blocks = len(command_data_blocks())
    if (
        command_blocks <= 0
        or command_blocks > available_blocks
        or (result_blocks and command_blocks + result_blocks > available_blocks)
    ):
        return CommandCardMetadata(
            int(raw[4]),
            command_blocks,
            result_blocks,
            int(raw[7]),
            raw[8:16].hex().upper(),
            valid=False,
        )
    provenance = raw[8:16].hex().upper()
    return CommandCardMetadata(
        version=int(raw[4]),
        command_block_count=command_blocks,
        result_block_count=result_blocks,
        flags=int(raw[7]),
        provenance_key="" if provenance == "0000000000000000" else provenance,
        valid=int(raw[4]) == COMMAND_LAYOUT_VERSION,
    )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode(COMMAND_PAYLOAD_ENCODING)


def result_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalized_command_payload(
    *,
    command: str,
    params: dict[str, Any] | None = None,
    sigils: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_command = str(command or "").strip().upper()
    if not normalized_command:
        raise CardLayoutError("command name is required")
    if params is not None and not isinstance(params, dict):
        raise CardLayoutError("command params must be a JSON object")
    if sigils is not None and not isinstance(sigils, dict):
        raise CardLayoutError("command sigils must be a JSON object")
    payload: dict[str, Any] = {
        "command": normalized_command,
        "params": params or {},
    }
    if sigils:
        payload["sigils"] = sigils
    return payload


def command_payload_digest(
    *,
    name: str,
    command: str,
    params: dict[str, Any] | None = None,
    sigils: dict[str, Any] | None = None,
) -> str:
    return result_digest(
        {
            "name": normalize_card_name(name),
            "payload": _normalized_command_payload(
                command=command,
                params=params,
                sigils=sigils,
            ),
        }
    )


def command_payload_digest_for_card(card: DecodedCommandCard) -> str:
    raw_command = card.raw_command if isinstance(card.raw_command, dict) else {}
    return result_digest(
        {
            "name": normalize_card_name(card.name),
            "payload": raw_command,
        }
    )


def _json_from_bytes(data: bytes) -> dict[str, Any]:
    stripped = data.rstrip(b"\x00 ")
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped.decode(COMMAND_PAYLOAD_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _block_map_from_dump(dump: object) -> dict[int, list[int]]:
    blocks: dict[int, list[int]] = {}
    if not isinstance(dump, list):
        return blocks
    for entry in dump:
        if not isinstance(entry, dict):
            continue
        block = entry.get("block")
        data = entry.get("data")
        if not isinstance(block, int) or not isinstance(data, (list, tuple)):
            continue
        values: list[int] = []
        for value in list(data)[:BLOCK_SIZE]:
            try:
                values.append(int(value) & 0xFF)
            except (TypeError, ValueError):
                values.append(0)
        values.extend([0] * (BLOCK_SIZE - len(values)))
        blocks[block] = values
    return blocks


def encode_command_payload(
    *,
    command: str,
    params: dict[str, Any] | None = None,
    sigils: dict[str, Any] | None = None,
) -> tuple[list[int], int]:
    payload = _normalized_command_payload(
        command=command,
        params=params,
        sigils=sigils,
    )
    encoded = canonical_json_bytes(payload)
    block_count = max(1, math.ceil(len(encoded) / BLOCK_SIZE))
    capacity = max(0, (len(command_data_blocks()) - MIN_RESULT_BLOCKS) * BLOCK_SIZE)
    if len(encoded) > capacity:
        raise CardLayoutError("command payload must leave room for result data")
    padded = encoded.ljust(block_count * BLOCK_SIZE, b"\x00")
    return list(padded), block_count


def encode_result_payload(
    payload: dict[str, Any],
    *,
    command_block_count: int,
) -> tuple[list[int], str, dict[str, Any]]:
    blocks = result_data_blocks(command_block_count)
    capacity = len(blocks) * BLOCK_SIZE
    if capacity <= 0:
        raise CardLayoutError("no result blocks are available")
    digest = result_digest(payload)
    encoded = canonical_json_bytes(payload)
    stored_payload = dict(payload)
    if len(encoded) > capacity:
        stored_payload = {
            "status": str(payload.get("status") or "")[:16],
            "ok": bool(payload.get("ok", False)),
            "summary": str(payload.get("summary") or "")[:80],
            "digest": digest,
            "ref": str(payload.get("ref") or payload.get("execution_id") or "")[:64],
            "truncated": True,
        }
        encoded = canonical_json_bytes(stored_payload)
        if len(encoded) > capacity:
            stored_payload["summary"] = str(stored_payload.get("summary") or "")[:16]
            encoded = canonical_json_bytes(stored_payload)
        if len(encoded) > capacity:
            raise CardLayoutError("result payload summary does not fit on this card")
    padded = encoded.ljust(capacity, b"\x00")
    return list(padded), result_digest(stored_payload), stored_payload


def result_data_blocks(command_block_count: int) -> list[int]:
    return command_data_blocks()[max(0, int(command_block_count)) :]


def build_command_card_blocks(
    *,
    name: str,
    command: str,
    params: dict[str, Any] | None = None,
    sigils: dict[str, Any] | None = None,
    provenance_key: str = "",
    lifecycle_mode: str = COMMAND_LIFECYCLE_TRIGGERED,
) -> tuple[dict[int, list[int]], CommandCardMetadata]:
    encoded_command, command_blocks = encode_command_payload(
        command=command,
        params=params,
        sigils=sigils,
    )
    data_blocks = command_data_blocks()
    if len(data_blocks) - command_blocks < MIN_RESULT_BLOCKS:
        raise CardLayoutError("command payload must leave room for result data")
    result_blocks = len(data_blocks) - command_blocks
    metadata_block = encode_command_metadata(
        command_block_count=command_blocks,
        result_block_count=result_blocks,
        flags=flags_for_lifecycle_mode(lifecycle_mode),
        provenance_key=provenance_key,
    )
    payload: dict[int, list[int]] = {
        COMMAND_CARD_NAME_BLOCK: encode_card_name(name),
        COMMAND_METADATA_BLOCK: metadata_block,
    }
    for index in range(command_blocks):
        start = index * BLOCK_SIZE
        payload[data_blocks[index]] = encoded_command[start : start + BLOCK_SIZE]
    for block in data_blocks[command_blocks:]:
        payload[block] = zero_block()
    return payload, decode_command_metadata(metadata_block)


def decode_command_card_from_dump(dump: object) -> DecodedCommandCard | None:
    blocks = _block_map_from_dump(dump)
    name = ""
    name_block = blocks.get(COMMAND_CARD_NAME_BLOCK)
    if name_block:
        from apps.cards.classic_layout import decode_card_name

        name = decode_card_name(name_block)
    metadata = decode_command_metadata(blocks.get(COMMAND_METADATA_BLOCK, zero_block()))
    if not metadata.valid:
        return None
    data_blocks = command_data_blocks()
    command_bytes = bytearray()
    for block in data_blocks[: metadata.command_block_count]:
        command_bytes.extend(blocks.get(block, zero_block()))
    command_payload = _json_from_bytes(bytes(command_bytes))
    command_name = str(command_payload.get("command") or "").strip().upper()
    params = command_payload.get("params")
    sigils = command_payload.get("sigils")
    result_bytes = bytearray()
    for block in result_data_blocks(metadata.command_block_count):
        result_bytes.extend(blocks.get(block, zero_block()))
    return DecodedCommandCard(
        name=name,
        metadata=metadata,
        command=command_name,
        params=params if isinstance(params, dict) else {},
        sigils=sigils if isinstance(sigils, dict) else {},
        raw_command=command_payload,
        result=_json_from_bytes(bytes(result_bytes)),
    )


def command_payload_blocks_complete(dump: object) -> bool:
    """Return whether dump contains enough framed blocks to decode a command."""

    blocks = _block_map_from_dump(dump)
    metadata = decode_command_metadata(blocks.get(COMMAND_METADATA_BLOCK, zero_block()))
    if not metadata.valid:
        return False
    required_blocks = {COMMAND_CARD_NAME_BLOCK, COMMAND_METADATA_BLOCK}
    required_blocks.update(command_data_blocks()[: metadata.command_block_count])
    if not required_blocks.issubset(blocks):
        return False
    card = decode_command_card_from_dump(dump)
    return bool(card is not None and card.command)


def command_result_blocks_complete(dump: object) -> bool:
    """Return whether dump contains the declared command-result block range."""

    blocks = _block_map_from_dump(dump)
    metadata = decode_command_metadata(blocks.get(COMMAND_METADATA_BLOCK, zero_block()))
    if not metadata.valid:
        return False
    if metadata.result_block_count <= 0:
        return True
    required_blocks = set(
        result_data_blocks(metadata.command_block_count)[
            : metadata.result_block_count
        ]
    )
    return required_blocks.issubset(blocks)
