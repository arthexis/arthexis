"""MIFARE block I/O helpers for the card reader runtime."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager

from apps.cards.classic_layout import FACTORY_KEY

MFRC522_AUTH_LOGGER_NAMES = ("mfrc522Logger", "mfrc522")
MFRC522_EXPECTED_AUTH_MESSAGES = (
    "AUTH ERROR!!",
    "AUTH ERROR(status2reg & 0x08) != 0",
)

COMMON_MIFARE_CLASSIC_KEYS = (
    FACTORY_KEY,
    "A0A1A2A3A4A5",
    "B0B1B2B3B4B5",
    "000000000000",
    "D3F7D3F7D3F7",
    "AABBCCDDEEFF",
    "1A2B3C4D5E6F",
    "4D3A99C351DD",
    "123456789ABC",
    "ABCDEF123456",
)

_KEY_RE = re.compile(r"^[0-9A-F]{12}$")


class ExpectedMifareAuthFailureFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().strip()
        return not any(
            message.startswith(expected)
            for expected in MFRC522_EXPECTED_AUTH_MESSAGES
        )


@contextmanager
def suppress_expected_mifare_auth_logs():
    filters = [
        (logging.getLogger(name), ExpectedMifareAuthFailureFilter())
        for name in MFRC522_AUTH_LOGGER_NAMES
    ]
    for target_logger, log_filter in filters:
        target_logger.addFilter(log_filter)
    try:
        yield
    finally:
        for target_logger, log_filter in filters:
            target_logger.removeFilter(log_filter)


def normalize_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _KEY_RE.fullmatch(candidate):
        return None
    return candidate


def key_to_bytes(value: str) -> list[int] | None:
    if not _KEY_RE.fullmatch(value):
        return None
    try:
        return [int(value[i : i + 2], 16) for i in range(0, 12, 2)]
    except ValueError:  # pragma: no cover - defensive guard
        return None


def read_block(
    mfrc,
    *,
    block: int,
    key_type: str,
    key_bytes: list[int],
    uid: list[int],
) -> list[int] | None:
    if key_type == "B":
        auth_mode = mfrc.PICC_AUTHENT1B
    else:
        auth_mode = mfrc.PICC_AUTHENT1A
    status = mfrc.MFRC522_Auth(auth_mode, block, key_bytes, uid)
    if status != mfrc.MI_OK:
        return None
    read_status = mfrc.MFRC522_Read(block)
    if isinstance(read_status, tuple):
        read_status, data = read_status
    else:
        data = read_status
        read_status = mfrc.MI_OK
    if read_status != mfrc.MI_OK or data is None:
        return None
    return list(data)


def is_sector_trailer_block(block: int) -> bool:
    if block < 0:
        return False
    if block < 128:
        return block % 4 == 3
    return (block - 128) % 16 == 15


def write_block(
    mfrc,
    *,
    block: int,
    key_type: str,
    key_bytes: list[int],
    uid: list[int],
    data: list[int],
) -> bool:
    if key_type == "B":
        auth_mode = mfrc.PICC_AUTHENT1B
    else:
        auth_mode = mfrc.PICC_AUTHENT1A
    status = mfrc.MFRC522_Auth(auth_mode, block, key_bytes, uid)
    if status != mfrc.MI_OK:
        return False
    write_fn = getattr(mfrc, "MFRC522_Write", None)
    if not callable(write_fn):
        return False
    expected_data = list(data)[:16]
    write_status = write_fn(block, expected_data)
    if isinstance(write_status, tuple):
        write_status = write_status[0]
    if write_status == mfrc.MI_OK:
        return True
    if write_status is None:
        if is_sector_trailer_block(block):
            return True
        readback = read_block(
            mfrc,
            block=block,
            key_type=key_type,
            key_bytes=key_bytes,
            uid=uid,
        )
        return readback is not None and readback[:16] == expected_data
    return False
