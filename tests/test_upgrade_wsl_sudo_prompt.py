"""Regression tests for WSL sudo priming in the upgrade script."""

from pathlib import Path

import pytest


pytestmark = [pytest.mark.critical, pytest.mark.regression]


def test_upgrade_script_sources_common_helper() -> None:
    """The upgrade script should source common helpers used for sudo priming."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert '. "$BASE_DIR/scripts/helpers/common.sh"' in script_text


def test_upgrade_script_primes_wsl_sudo_credentials() -> None:
    """The upgrade script should prime sudo credentials before systemd actions."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert "arthexis_prime_sudo_credentials" in script_text


def test_upgrade_script_skips_sudo_priming_for_check_mode() -> None:
    """The sudo prompt should be gated so read-only --check does not block."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    parse_start = script_text.index("while [[ $# -gt 0 ]]; do\n")
    parse_done = script_text.index("done\n", parse_start)
    gate = "if [[ $CHECK_ONLY -ne 1 ]]; then\n  arthexis_prime_sudo_credentials >/dev/null 2>&1 || true\nfi"

    assert gate in script_text
    assert script_text.index(gate) > parse_done


def test_upgrade_script_cleans_generated_merge_migrations() -> None:
    """Regression: generated merge migrations should be removed before dirty check."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert "Removing generated migration" in script_text
    assert (
        "^apps/[^/]+/migrations/[0-9]+_merge_[0-9]{8}_[0-9]{4}\\.py$"
        in script_text
    )
