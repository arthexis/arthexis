from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.gate_upgrade]

ROOT = Path(__file__).resolve().parents[3]


def _script(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _assert_pattern(script: str, pattern: str) -> None:
    assert re.search(pattern, script, re.MULTILINE | re.DOTALL)


def test_suite_up_supports_green_protection_and_target_selection() -> None:
    script = _script("scripts/suite-up.sh")

    _assert_pattern(script, r'INCLUDE_GREEN="\$\{SUITE_UP_INCLUDE_GREEN:-0\}"')
    _assert_pattern(script, r'ONLY_TARGET="\$\{SUITE_UP_ONLY:-\}"')
    _assert_pattern(script, r'SUITE_UP_TARGETS="\$\{SUITE_UP_TARGETS:-[^}]+\}"')
    _assert_pattern(script, r'SUITE_UP_GREEN_TARGETS="\$\{SUITE_UP_GREEN_TARGETS:-[^}]+\}"')
    _assert_pattern(
        script,
        r'if\s+is_green\s+"\$target"\s*&&\s*\[\[\s*"\$INCLUDE_GREEN"\s*-ne\s*1\s*\]\]\s*;\s*then',
    )


def test_suite_up_restart_unchanged_toggle() -> None:
    script = _script("scripts/suite-up.sh")

    _assert_pattern(script, r'RESTART_UNCHANGED="\$\{SUITE_UP_RESTART_UNCHANGED:-0\}"')
    _assert_pattern(script, r'cmd\+=\("--pre-check"\)')
    _assert_pattern(script, r'if\s+\[\[\s*"\$RESTART_UNCHANGED"\s*-ne\s*1\s*\]\]\s*;\s*then')
    _assert_pattern(script, r'SERVICE_NAME="\$target"\s+"\$\{cmd\[@\]\}"')


def test_upgrade_honors_forwarded_service_name_before_lock_discovery() -> None:
    script = _script("upgrade.sh")

    _assert_pattern(script, r'SERVICE_NAME="\$\{SERVICE_NAME:-\}"')
    _assert_pattern(
        script,
        r'\[\[\s*-z\s+"\$SERVICE_NAME"\s*\]\]\s*&&\s*\[\[\s*-f\s+"\$LOCK_DIR/service\.lck"\s*\]\]',
    )
    _assert_pattern(
        script,
        r'local\s+service_name="\$\{SERVICE_NAME:-\}"\s*\n\s*if\s+\[\[\s+-z\s+"\$service_name"\s*\]\]\s*&&\s*\[\[\s+-f\s+"\$LOCK_DIR/service\.lck"\s*\]\]',
    )
    _assert_pattern(
        script,
        r'if\s+\[\[\s+-z\s+"\$SERVICE_NAME"\s*\]\]\s*&&\s*\[\[\s+-f\s+"\$LOCK_DIR/service\.lck"\s*\]\]\s*;\s*then\s*\n\s*SERVICE_NAME="\$\(cat\s+"\$LOCK_DIR/service\.lck"\)"\s*\n\s*fi\s*\n\s*if\s+\[\s+-n\s+"\$SERVICE_NAME"\s*\]\s*;\s*then',
    )
