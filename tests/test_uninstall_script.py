from pathlib import Path




def test_uninstall_script_removes_wifi_watchdog():
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "wifi-watchdog" in content
