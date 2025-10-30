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
