"""Sigil token scanning helpers.

LLVM-backed scanning has been deprecated; Arthexis now always uses the
in-process Python scanner to keep behavior predictable across nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class TokenSpan:
    """A span representing one full sigil token including enclosing brackets."""

    start: int
    end: int


class _PythonScanner:
    """Reference scanner used for sigil token detection."""

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


@lru_cache(maxsize=1)
def get_scanner() -> _PythonScanner:
    """Return the active scanner backend (always Python)."""

    return _PythonScanner()


def scan_sigil_tokens(text: str) -> list[TokenSpan]:
    """Scan ``text`` and return token spans using the active scanner backend."""

    return get_scanner().scan(text)
