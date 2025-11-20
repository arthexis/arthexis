from pathlib import Path




def test_uninstall_script_no_longer_manages_wifi_watchdog() -> None:
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()
    assert "wifi-watchdog" not in content


def test_uninstall_script_handles_tracked_services() -> None:
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text()

    assert "systemd_services.lck" in content
    assert "upgrade-guard" in content


def test_uninstall_script_preserves_user_data_fixtures() -> None:
    script_path = Path(__file__).resolve().parent.parent / "uninstall.sh"
    content = script_path.read_text().splitlines()

    assert any("Preserving user data fixtures" in line for line in content)

    destructive_data_lines = [
        line
        for line in content
        if "rm" in line and ("$DATA_DIR" in line or "data/" in line)
    ]
    assert destructive_data_lines == []
