"""Sigil token scanners with optional LLVM native acceleration.

This module keeps the parser regex-free while allowing deployment-time acceleration
via a tiny native library compiled with clang/LLVM.

Native ABI contract (all indexes are byte offsets in UTF-8 compatible input):

.. code-block:: c

    // Returns number of token pairs written to `out_pairs`.
    // Each pair is [start_index, end_index_exclusive], where the full token is
    // text[start:end] and includes the surrounding brackets.
    uint32_t scan_sigil_tokens(
        const char* text,
        uint64_t text_len,
        uint32_t* out_pairs,
        uint32_t max_pairs
    );

If the native scanner is unavailable or invalid, we transparently fall back to
an in-process Python scanner that preserves nested bracket behavior.
"""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)


class SigilScannerError(RuntimeError):
    """Raised when a configured sigil scanner backend cannot be initialized."""


@dataclass(frozen=True)
class TokenSpan:
    """A span representing one full sigil token including enclosing brackets."""

    start: int
    end: int


class _PythonScanner:
    """Reference scanner used as the fallback backend."""

    @staticmethod
    def scan(text: str) -> list[TokenSpan]:
        tokens: list[TokenSpan] = []
        index = 0
        text_length = len(text)
        while index < text_length:
            if text[index] != "[":
                index += 1
                continue
            depth = 1
            cursor = index + 1
            while cursor < text_length and depth:
                if text[cursor] == "[":
                    depth += 1
                elif text[cursor] == "]":
                    depth -= 1
                cursor += 1
            if depth == 0:
                tokens.append(TokenSpan(start=index, end=cursor))
                index = cursor
                continue
            index += 1
        return tokens


class _LlvmScanner:
    """Optional scanner backed by a shared object produced by clang/LLVM."""

    def __init__(self, library_path: str) -> None:
        if not library_path:
            raise SigilScannerError("SIGIL_LLVM_LIBRARY is required for llvm backend")
        try:
            self._library = ctypes.CDLL(library_path)
        except OSError as exc:
            raise SigilScannerError(f"Unable to load LLVM scanner library: {library_path}") from exc

        try:
            scanner = self._library.scan_sigil_tokens
        except AttributeError as exc:
            raise SigilScannerError(
                "LLVM scanner library is missing required symbol scan_sigil_tokens"
            ) from exc

        scanner.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        scanner.restype = ctypes.c_uint32
        self._scanner = scanner

    def scan(self, text: str) -> list[TokenSpan]:
        if not text:
            return []
        encoded = text.encode("utf-8")
        max_pairs = max(1, len(text))
        pair_buffer = (ctypes.c_uint32 * (max_pairs * 2))()
        pair_count = int(
            self._scanner(
                encoded,
                ctypes.c_uint64(len(encoded)),
                pair_buffer,
                ctypes.c_uint32(max_pairs),
            )
        )
        tokens: list[TokenSpan] = []
        byte_to_char = self._build_utf8_byte_to_char_index(text)
        for idx in range(pair_count):
            start = int(pair_buffer[idx * 2])
            end = int(pair_buffer[idx * 2 + 1])
            if end > len(encoded) or start >= end:
                logger.warning("Ignoring invalid LLVM token span (%s, %s)", start, end)
                continue
            start_char = byte_to_char[start]
            end_char = byte_to_char[end]
            if start_char < 0 or end_char < 0:
                logger.warning(
                    "Ignoring non-boundary LLVM token span (%s, %s)", start, end
                )
                continue
            tokens.append(TokenSpan(start=start_char, end=end_char))
        return tokens

    @staticmethod
    def _build_utf8_byte_to_char_index(text: str) -> list[int]:
        encoded_length = len(text.encode("utf-8"))
        byte_to_char = [-1] * (encoded_length + 1)
        byte_index = 0
        byte_to_char[0] = 0
        for char_index, char in enumerate(text, start=1):
            byte_index += len(char.encode("utf-8"))
            byte_to_char[byte_index] = char_index
        return byte_to_char


@lru_cache(maxsize=1)
def get_scanner():
    """Return the active scanner backend according to environment configuration."""

    backend = os.environ.get("SIGIL_SCANNER_BACKEND", "llvm").strip().lower()
    if backend == "llvm":
        library_path = os.environ.get("SIGIL_LLVM_LIBRARY", "").strip()
        try:
            scanner = _LlvmScanner(library_path=library_path)
            logger.info("Using LLVM sigil scanner backend from %s", library_path)
            return scanner
        except SigilScannerError:
            logger.exception("Falling back to Python sigil scanner backend")
    return _PythonScanner()


def scan_sigil_tokens(text: str) -> list[TokenSpan]:
    """Scan ``text`` and return token spans using the active scanner backend."""

    return get_scanner().scan(text)
