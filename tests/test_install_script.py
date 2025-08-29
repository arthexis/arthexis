from pathlib import Path

def test_install_script_runs_migrate():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "python manage.py migrate" in content


def test_install_script_includes_terminal_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--terminal" in content


def test_install_script_includes_constellation_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--constellation" in content

