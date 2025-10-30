from pathlib import Path




def test_uninstall_script_no_longer_manages_wifi_watchdog() -> None:
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "wifi-watchdog" not in content
