from pathlib import Path


def _read_upgrade_script() -> str:
    script_path = Path(__file__).resolve().parent.parent / "upgrade.sh"
    return script_path.read_text()


def test_upgrade_script_updates_python_dependencies_when_requirements_change() -> None:
    content = _read_upgrade_script()
    assert "requirements.md5" in content
    assert "pip install -r \"$req_file\"" in content


def test_upgrade_script_creates_virtualenv_if_missing() -> None:
    content = _read_upgrade_script()
    assert "ensure_virtualenv" in content
    assert "-m venv \"$venv_dir\"" in content


def test_upgrade_script_runs_database_migrations() -> None:
    content = _read_upgrade_script()
    assert "python manage.py migrate --noinput" in content


def test_upgrade_script_reloads_personal_fixtures() -> None:
    content = _read_upgrade_script()
    assert "python manage.py loaddata data/*.json" in content


def test_upgrade_script_applies_nginx_configuration() -> None:
    content = _read_upgrade_script()
    assert "nginx-setup.sh" in content
    assert "configure_nginx_site" in content
