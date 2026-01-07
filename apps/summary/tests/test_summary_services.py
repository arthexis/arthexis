from pathlib import Path

import pytest

from apps.summary.services import (
    LogChunk,
    compact_log_chunks,
    normalize_screens,
    parse_screens,
)


@pytest.mark.django_db
def test_compact_log_chunks_rewrites_tokens(tmp_path):
    log_path = tmp_path / "example.log"
    chunk = LogChunk(
        path=log_path,
        content=(
            "2024-01-01 00:00:00 INFO user=demo id=123e4567-e89b-12d3-a456-426614174000 "
            "addr=192.168.0.1 token=abcdefabcdefabcdef\n"
        ),
    )
    output = compact_log_chunks([chunk])

    assert "[example.log]" in output
    assert "<uuid>" in output
    assert "<ip>" in output
    assert "<hex>" in output
    assert "INF" in output


def test_parse_and_normalize_screens():
    raw = """
    SCREEN 1:
    System Alert: Action Required
    Reboot svc now
    ---
    SCREEN 2:
    OK
    All clear
    """
    screens = normalize_screens(parse_screens(raw))

    assert len(screens) == 2
    for subject, body in screens:
        assert len(subject) == 16
        assert len(body) == 16
