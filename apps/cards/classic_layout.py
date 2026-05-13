from __future__ import annotations

import re
import secrets
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any

FACTORY_KEY = "FFFFFFFFFFFF"
BLOCK_SIZE = 16
SECTOR_DATA_BLOCKS = 3
SECTOR_TRAILER_OFFSET = 3
PRESERVED_SECTORS = {0, 1}
FIRST_MANAGED_SECTOR = 3
LAST_MANAGED_SECTOR = 15
FIRST_TRAIT_SECTOR = 3
LAST_TRAIT_SECTOR = 14
LCD_LABEL_SECTOR = 0
LCD_LABEL_BLOCK_OFFSETS = (1, 2)
WRITER_SECTOR = 1
WRITER_ID_BLOCK_OFFSET = 1
WRITER_DATE_BLOCK_OFFSET = 2
TRAIT_KEY_BYTES = 16
TRAIT_VALUE_BYTES = 80
WRITER_ID_BYTES = 16
WRITER_DATE_BYTES = 16
LCD_LINE_BYTES = 16
LCD_LABEL_BYTES = LCD_LINE_BYTES * 2
_KEY_RE = re.compile(r"^[0-9A-F]{12}$")
_ASCII_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_SIGIL_ENV_RE = re.compile(r"[^A-Z0-9]+")


class CardLayoutError(ValueError):
    """Raised when a card layout value cannot fit the configured format."""


def utc_now() -> datetime:
    return datetime.now(datetime_timezone.utc)


def random_classic_key() -> str:
    return secrets.token_hex(6).upper()


def normalize_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if _KEY_RE.fullmatch(candidate):
        return candidate
    return None


def key_to_bytes(value: str) -> list[int]:
    normalized = normalize_key(value)
    if not normalized:
        raise CardLayoutError("RFID key must be 12 hexadecimal digits")
    return [int(normalized[index : index + 2], 16) for index in range(0, 12, 2)]


def sector_numbers() -> range:
    return range(0, LAST_MANAGED_SECTOR + 1)


def managed_sector_numbers() -> range:
    return range(FIRST_MANAGED_SECTOR, LAST_MANAGED_SECTOR + 1)


def trait_sector_pairs() -> list[tuple[int, int]]:
    return [
        (sector, sector + 1)
        for sector in range(FIRST_TRAIT_SECTOR, LAST_TRAIT_SECTOR + 1, 2)
        if sector + 1 <= LAST_TRAIT_SECTOR
    ]


def sector_block(sector: int, offset: int = 0) -> int:
    if sector < 0:
        raise CardLayoutError("sector must be non-negative")
    if sector < 32:
        if offset < 0 or offset > SECTOR_TRAILER_OFFSET:
            raise CardLayoutError("sector offset must be between 0 and 3")
        return sector * 4 + offset
    if offset < 0 or offset > 15:
        raise CardLayoutError("large-sector offset must be between 0 and 15")
    return 128 + ((sector - 32) * 16) + offset


def sector_data_blocks(sector: int) -> list[int]:
    if sector < 32:
        return [sector_block(sector, offset) for offset in range(SECTOR_DATA_BLOCKS)]
    return [sector_block(sector, offset) for offset in range(15)]


def sector_trailer_block(sector: int) -> int:
    return sector_block(sector, SECTOR_TRAILER_OFFSET if sector < 32 else 15)


def scan_block_count() -> int:
    return sector_trailer_block(LAST_MANAGED_SECTOR) + 1


def clean_ascii_text(value: object, *, allow_newlines: bool = False) -> str:
    text = "" if value is None else str(value)
    if not allow_newlines:
        text = text.replace("\r", " ").replace("\n", " ")
    else:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ASCII_CONTROL_RE.sub("", text)


def encode_fixed_ascii(value: object, length: int, *, allow_newlines: bool = False) -> list[int]:
    text = clean_ascii_text(value, allow_newlines=allow_newlines)
    encoded = text.encode("ascii", errors="ignore")
    if len(encoded) > length:
        raise CardLayoutError(f"value must fit in {length} ASCII bytes")
    return list(encoded.ljust(length, b"\x00"))


def decode_fixed_ascii(data: list[int] | tuple[int, ...] | bytes | bytearray) -> str:
    raw = bytes(int(value) & 0xFF for value in data)
    return raw.rstrip(b"\x00 ").decode("ascii", errors="ignore")


def lcd_label_lines(value: object) -> tuple[str, str]:
    text = clean_ascii_text(value, allow_newlines=True)
    parts = text.split("\n", 1)
    line_1 = parts[0][:LCD_LINE_BYTES]
    line_2 = (parts[1] if len(parts) > 1 else "")[:LCD_LINE_BYTES]
    return line_1, line_2


def normalize_lcd_label(value: object) -> str:
    line_1, line_2 = lcd_label_lines(value)
    return "\n".join((line_1, line_2)).rstrip("\n")


def encode_lcd_label(value: object) -> list[int]:
    line_1, line_2 = lcd_label_lines(value)
    return list(line_1.encode("ascii", errors="ignore").ljust(LCD_LINE_BYTES, b"\x00")) + list(
        line_2.encode("ascii", errors="ignore").ljust(LCD_LINE_BYTES, b"\x00")
    )


def decode_lcd_label(data: list[int] | tuple[int, ...] | bytes | bytearray) -> str:
    raw = list(data)[:LCD_LABEL_BYTES]
    if len(raw) < LCD_LABEL_BYTES:
        raw.extend([0] * (LCD_LABEL_BYTES - len(raw)))
    line_1 = decode_fixed_ascii(raw[:LCD_LINE_BYTES])
    line_2 = decode_fixed_ascii(raw[LCD_LINE_BYTES:LCD_LABEL_BYTES])
    return "\n".join((line_1, line_2)).rstrip("\n")


def default_writer_id() -> str:
    return "ARTHEXIS"


def normalize_writer_id(value: object | None = None) -> str:
    text = clean_ascii_text(value or default_writer_id())
    encoded = text.encode("ascii", errors="ignore")
    if len(encoded) > WRITER_ID_BYTES:
        raise CardLayoutError("writer id must fit in 16 ASCII bytes")
    return encoded.decode("ascii")


def encode_writer_id(value: object | None = None) -> list[int]:
    return encode_fixed_ascii(normalize_writer_id(value), WRITER_ID_BYTES)


def encode_writer_date(value: datetime | None = None) -> list[int]:
    timestamp = value or utc_now()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime_timezone.utc)
    timestamp = timestamp.astimezone(datetime_timezone.utc)
    return list(timestamp.strftime("%Y%m%dT%H%M%SZ").encode("ascii"))


def decode_writer_date(data: list[int] | tuple[int, ...] | bytes | bytearray) -> str:
    return decode_fixed_ascii(data[:WRITER_DATE_BYTES])


def zero_block() -> list[int]:
    return [0] * BLOCK_SIZE


def zero_sector_data() -> dict[int, list[int]]:
    return {offset: zero_block() for offset in range(SECTOR_DATA_BLOCKS)}


def normalize_sector_keys(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_sector, raw_record in value.items():
        try:
            sector = int(raw_sector)
        except (TypeError, ValueError):
            continue
        if sector < FIRST_MANAGED_SECTOR:
            continue
        if not isinstance(raw_record, dict):
            continue
        key_a = normalize_key(raw_record.get("key_a") or raw_record.get("a"))
        key_b = normalize_key(raw_record.get("key_b") or raw_record.get("b"))
        if not key_a or not key_b:
            continue
        normalized[str(sector)] = {"key_a": key_a, "key_b": key_b}
    return normalized


def ensure_sector_key_records(value: object) -> dict[str, dict[str, str]]:
    records = normalize_sector_keys(value)
    for sector in managed_sector_numbers():
        records.setdefault(
            str(sector),
            {"key_a": random_classic_key(), "key_b": random_classic_key()},
        )
    return records


def sector_key_record(value: object, sector: int) -> dict[str, str] | None:
    records = normalize_sector_keys(value)
    return records.get(str(sector))


def build_sector_trailer(key_a: str, key_b: str) -> list[int]:
    # Data blocks stay transport-compatible so both keys can read/write at the
    # card layer; the suite treats Key A as the writer key and Key B as reader
    # only by policy.
    return key_to_bytes(key_a) + [0xFF, 0x07, 0x80, 0x69] + key_to_bytes(key_b)


def normalize_trait_key(value: object) -> str:
    text = clean_ascii_text(value)
    encoded = text.encode("ascii", errors="ignore")
    if not encoded:
        raise CardLayoutError("trait key is required")
    if len(encoded) > TRAIT_KEY_BYTES:
        raise CardLayoutError("trait key must fit in 16 ASCII bytes")
    return encoded.decode("ascii")


def normalize_trait_value(value: object) -> str:
    text = clean_ascii_text(value, allow_newlines=True)
    encoded = text.encode("ascii", errors="ignore")
    if len(encoded) > TRAIT_VALUE_BYTES:
        raise CardLayoutError("trait value must fit in 80 ASCII bytes")
    return encoded.decode("ascii")


def trait_sigil_name(key: object) -> str:
    normalized = normalize_trait_key(key).upper()
    env_name = _SIGIL_ENV_RE.sub("_", normalized).strip("_")
    return f"SIGIL_{env_name}" if env_name else "SIGIL_TRAIT"


def encode_trait_key(value: object) -> list[int]:
    return encode_fixed_ascii(normalize_trait_key(value), TRAIT_KEY_BYTES)


def encode_trait_value(value: object) -> list[int]:
    return encode_fixed_ascii(
        normalize_trait_value(value),
        TRAIT_VALUE_BYTES,
        allow_newlines=True,
    )


def build_trait_block_payloads(start_sector: int, key: object, value: object) -> dict[int, list[int]]:
    pair = (start_sector, start_sector + 1)
    if pair not in trait_sector_pairs():
        raise CardLayoutError("trait must start on a configured trait sector pair")
    encoded_value = encode_trait_value(value)
    blocks = [
        encode_trait_key(key),
        encoded_value[0:16],
        encoded_value[16:32],
        encoded_value[32:48],
        encoded_value[48:64],
        encoded_value[64:80],
    ]
    return {
        sector_block(start_sector, 0): blocks[0],
        sector_block(start_sector, 1): blocks[1],
        sector_block(start_sector, 2): blocks[2],
        sector_block(start_sector + 1, 0): blocks[3],
        sector_block(start_sector + 1, 1): blocks[4],
        sector_block(start_sector + 1, 2): blocks[5],
    }


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
        block_data: list[int] = []
        for value in list(data)[:BLOCK_SIZE]:
            try:
                block_data.append(max(0, min(255, int(value))))
            except (TypeError, ValueError):
                block_data.append(0)
        if len(block_data) < BLOCK_SIZE:
            block_data.extend([0] * (BLOCK_SIZE - len(block_data)))
        blocks[block] = block_data
    return blocks


def decode_transport_metadata(dump: object) -> dict[str, Any]:
    blocks = _block_map_from_dump(dump)
    label_data: list[int] = []
    for offset in LCD_LABEL_BLOCK_OFFSETS:
        label_data.extend(blocks.get(sector_block(LCD_LABEL_SECTOR, offset), zero_block()))
    writer_id = decode_fixed_ascii(blocks.get(sector_block(WRITER_SECTOR, WRITER_ID_BLOCK_OFFSET), zero_block()))
    writer_date = decode_writer_date(blocks.get(sector_block(WRITER_SECTOR, WRITER_DATE_BLOCK_OFFSET), zero_block()))
    return {
        "lcd_label": decode_lcd_label(label_data),
        "writer": {
            "id": writer_id,
            "written_at": writer_date,
        },
    }


def decode_traits_from_dump(dump: object) -> dict[str, dict[str, Any]]:
    blocks = _block_map_from_dump(dump)
    traits: dict[str, dict[str, Any]] = {}
    for start_sector, continuation_sector in trait_sector_pairs():
        key_block = blocks.get(sector_block(start_sector, 0), zero_block())
        if all(value == 0 for value in key_block):
            continue
        key = decode_fixed_ascii(key_block)
        if not key:
            continue
        value_data: list[int] = []
        value_data.extend(blocks.get(sector_block(start_sector, 1), zero_block()))
        value_data.extend(blocks.get(sector_block(start_sector, 2), zero_block()))
        for offset in range(SECTOR_DATA_BLOCKS):
            value_data.extend(blocks.get(sector_block(continuation_sector, offset), zero_block()))
        value = decode_fixed_ascii(value_data[:TRAIT_VALUE_BYTES])
        traits[key] = {
            "value": value,
            "sector": start_sector,
            "sectors": [start_sector, continuation_sector],
        }
    return traits


def normalize_trait_records(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for raw_key, raw_record in value.items():
        try:
            key = normalize_trait_key(raw_key)
        except CardLayoutError:
            continue
        if isinstance(raw_record, dict):
            raw_value = raw_record.get("value", "")
            sector = raw_record.get("sector")
            sectors = raw_record.get("sectors")
        else:
            raw_value = raw_record
            sector = None
            sectors = None
        try:
            trait_value = normalize_trait_value(raw_value)
        except CardLayoutError:
            trait_value = clean_ascii_text(raw_value, allow_newlines=True)[:TRAIT_VALUE_BYTES]
        record: dict[str, Any] = {"value": trait_value}
        try:
            if sector is not None:
                record["sector"] = int(sector)
        except (TypeError, ValueError):
            pass
        if isinstance(sectors, (list, tuple)):
            try:
                record["sectors"] = [int(item) for item in sectors]
            except (TypeError, ValueError):
                pass
        if "sector" in record and "sectors" not in record:
            record["sectors"] = [record["sector"], record["sector"] + 1]
        records[key] = record
    return records


def first_empty_trait_sector(records: object) -> int | None:
    used: set[int] = set()
    for record in normalize_trait_records(records).values():
        for sector in record.get("sectors", []):
            try:
                used.add(int(sector))
            except (TypeError, ValueError):
                continue
    for start_sector, continuation_sector in trait_sector_pairs():
        if start_sector not in used and continuation_sector not in used:
            return start_sector
    return None


def trait_values(records: object) -> dict[str, str]:
    return {
        key: str(record.get("value", ""))
        for key, record in normalize_trait_records(records).items()
    }


def trait_sigils(records: object) -> dict[str, str]:
    return {
        trait_sigil_name(key): value
        for key, value in trait_values(records).items()
    }
