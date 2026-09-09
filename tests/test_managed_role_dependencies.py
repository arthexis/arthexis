import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CELERY_RUNTIME_DEPENDENCIES = {
    "celery==5.5.3",
    "channels-redis==4.3.0",
    "django-celery-beat==2.8.1",
    "redis==8.1.0",
}


def test_managed_roles_select_celery_extra() -> None:
    manifest = tomllib.loads((ROOT / "gway.toml").read_text(encoding="utf-8"))
    extras = manifest["install"]["extras"]

    assert extras["argument"] == "--role"
    assert extras["default"] == "Terminal"
    assert extras["state"] == ".locks/role.lck"
    assert extras["values"] == {
        "Control": ["celery"],
        "Satellite": ["celery"],
        "Terminal": [],
        "Watchtower": ["celery"],
    }


def test_terminal_runtime_omits_celery_dependencies() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = set(pyproject["project"]["dependencies"])
    celery_extra = set(pyproject["project"]["optional-dependencies"]["celery"])

    assert CELERY_RUNTIME_DEPENDENCIES.isdisjoint(runtime)
    assert CELERY_RUNTIME_DEPENDENCIES <= celery_extra


def test_terminal_settings_omit_reports_with_celery_runtime() -> None:
    settings_source = (ROOT / "config" / "settings" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert '"apps.reports"' in settings_source
    assert "if not CELERY_RUNTIME_ENABLED:" in settings_source
