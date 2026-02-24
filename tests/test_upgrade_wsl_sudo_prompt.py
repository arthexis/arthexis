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
