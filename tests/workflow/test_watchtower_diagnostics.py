from pathlib import Path

import pytest

from scripts.watchtower_diagnostics import (
    MAX_RAW_SNAPSHOTS,
    persist_raw,
    sanitize_line,
)


pytestmark = pytest.mark.workflow


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Authorization: Bearer abc123", "Authorization: [REDACTED]"),
        ("token=supersecret", "token=[REDACTED]"),
        ("https://user:pass@example.com/path", "https://[REDACTED]@example.com/path"),
        (
            "https://example.com/?x=1&token=secret&y=2",
            "https://example.com/?x=1&token=[REDACTED]&y=2",
        ),
        ("github_pat_abcdefghijklmnopqrstuvwxyz123456", "[REDACTED TOKEN]"),
        ("-----BEGIN PRIVATE KEY-----", "[REDACTED PRIVATE KEY MATERIAL]"),
    ],
)
def test_watchtower_diagnostic_sanitizer_redacts_sensitive_values(raw, expected):
    assert sanitize_line(raw) == expected


def test_raw_watchtower_snapshots_are_private_and_bounded(tmp_path):
    source = tmp_path / "input.log"
    source.write_text("failure\n", encoding="utf-8")
    raw_dir = tmp_path / "private"

    latest = None
    for index in range(MAX_RAW_SNAPSHOTS + 3):
        latest = persist_raw(
            source,
            raw_dir=raw_dir,
            run_id=str(index),
            attempt="1",
            step="runtime",
        )

    assert latest is not None
    assert raw_dir.stat().st_mode & 0o777 == 0o700
    assert latest.stat().st_mode & 0o777 == 0o600
    assert len(list(raw_dir.glob("watchtower-*.log"))) == MAX_RAW_SNAPSHOTS
