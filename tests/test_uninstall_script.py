from pathlib import Path


def test_uninstall_script_handles_datasette_lock():
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "datasette.lck" in content


def test_uninstall_script_removes_datasette_service():
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "datasette-$SERVICE" in content
    assert 'systemctl stop "$DATASETTE_SERVICE"' in content


def test_uninstall_script_removes_wifi_watchdog():
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "wifi-watchdog" in content
