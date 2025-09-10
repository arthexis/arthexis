from pathlib import Path

def test_switch_role_script_includes_datasette_flag():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert "--datasette" in content


def test_switch_role_script_controls_datasette_service():
    script_path = Path(__file__).resolve().parent.parent / "switch-role.sh"
    content = script_path.read_text()
    assert "datasette-$SERVICE" in content
    assert 'systemctl stop "datasette-$SERVICE"' in content
    assert 'systemctl start "datasette-$SERVICE"' in content
