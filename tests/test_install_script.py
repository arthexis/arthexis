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


def test_install_script_requires_nginx_for_roles():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    for role in ("satellite", "control", "constellation"):
        assert f'require_nginx "{role}"' in content

