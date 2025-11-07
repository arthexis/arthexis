from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_network_setup_help_includes_flags() -> None:
    script = REPO_ROOT / "network-setup.sh"
    result = subprocess.run(
        [
            "bash",
            str(script),
            "--help",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert "--interactive" in result.stdout
    assert "--unsafe" in result.stdout
    assert "--subnet" in result.stdout
    assert "--constellation" in result.stdout
    assert "--no-watchdog" not in result.stdout


def test_network_setup_firewall_ports_include_camera_stream() -> None:
    """Ensure the firewall validation checks the camera stream port."""

    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "PORTS=(22 21114 8554)" in script_contents


def test_network_setup_no_longer_mentions_wifi_watchdog() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "WiFi watchdog" not in script_contents


def test_network_setup_wlan1_drop_rule_removed() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "-o wlan1 -j DROP" not in script_contents
    assert "ensure_forwarding_rule wlan0 wlan1" in script_contents
    assert "ensure_forwarding_rule eth0 wlan1" in script_contents


def test_network_setup_includes_wireguard_package_install() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "ensure_pkg wg wireguard-tools" in script_contents


def test_network_setup_warns_when_wlan1_missing() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    expected_warning = (
        "Warning: device wlan1 not found; wlan0 and eth0 clients will not have upstream internet access."
    )
    assert expected_warning in script_contents


def test_network_setup_accounts_for_systemd_networkd() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "Normalize systemd-networkd default route" in script_contents
    assert "normalize_systemd_networkd_default_route" in script_contents
    assert "NetworkManager (nmcli) not available; showing systemd-networkd status summary." in script_contents


def test_network_setup_prefers_dot_one_gateway_when_present() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert "Multiple default gateways detected for $iface" in script_contents
    assert '[[ "$candidate" =~ ^([0-9]+\\.){3}1$ ]]' in script_contents


def test_network_setup_safe_mode_messages_reference_flag() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    assert 'skip_network_step "wlan0 access point configuration"' in script_contents
    assert "Skipping $desc while running in safe mode" in script_contents
    assert "Re-run with --unsafe to allow this step." in script_contents
    assert "--dhcp-reset requires --unsafe" in script_contents


def test_network_setup_restores_constellation_device_manifest() -> None:
    script = REPO_ROOT / "network-setup.sh"
    script_contents = script.read_text()
    occurrences = script_contents.count("restore_constellation_devices_from_manifest")
    assert occurrences >= 2
    assert "CONSTELLATION_DEVICE_MANIFEST" in script_contents
