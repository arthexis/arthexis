"""Regression tests for the nmcli setup helper script."""

from pathlib import Path
import subprocess


SCRIPT_PATH = Path("scripts/nmcli-setup.sh")


def test_nmcli_setup_script_has_valid_bash_syntax() -> None:
    """The setup script should parse successfully under bash."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_nmcli_setup_script_configures_expected_interfaces() -> None:
    """The setup script should pin internet and AP traffic to the intended interfaces."""
    contents = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'readonly INTERNET_IFACE="wlan0"' in contents
    assert 'readonly AP_IFACE="wlan1"' in contents
    assert 'connection.interface-name "$AP_IFACE"' in contents
    assert 'connection.interface-name "$INTERNET_IFACE"' in contents
