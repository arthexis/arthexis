from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
CELERY_RUNTIME_DEPENDENCIES = {
    "celery==5.5.3",
    "channels-redis==4.3.0",
    "django-celery-beat==2.8.1",
    "redis==8.1.0",
}


def test_managed_install_has_no_dependency_extras() -> None:
    manifest = tomllib.loads((ROOT / "gway.toml").read_text(encoding="utf-8"))

    assert "extras" not in manifest["install"]


def test_celery_runtime_dependencies_are_mandatory() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = set(pyproject["project"]["dependencies"])
    optional_dependencies = pyproject["project"].get("optional-dependencies", {})

    assert CELERY_RUNTIME_DEPENDENCIES <= runtime
    assert "celery" not in optional_dependencies


def test_energy_migrations_require_django_celery_beat() -> None:
    migration_source = (
        ROOT / "apps" / "energy" / "migrations" / "0004_initial.py"
    ).read_text(encoding="utf-8")

    assert "('django_celery_beat', '0020_googlecalendarprofile')" in migration_source
    assert "to='django_celery_beat.periodictask'" in migration_source
