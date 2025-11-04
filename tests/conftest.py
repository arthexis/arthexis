from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import django
from django.apps import apps
from django.core.management import call_command

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Keep a reference to the original setup function so we can call it lazily
_original_setup = django.setup


def safe_setup(*args, **kwargs):
    """Initialize Django and ensure the test database is migrated.

    Pytest does not use ``pytest-django`` for these tests, so simply calling
    :func:`django.setup` leaves the database without any tables. Many tests
    exercise ORM behaviour and expect the schema to exist. Configure Django to
    use the dedicated test database and run ``migrate`` the first time it is
    configured so the database file and tables are created automatically.
    """

    if not apps.ready:
        from django.conf import settings

        # Switch to the test database defined in settings to avoid touching
        # development data.
        test_name = settings.DATABASES["default"].get("TEST", {}).get("NAME")
        if test_name:
            db_name = settings.DATABASES["default"]["NAME"]
            settings.DATABASES["default"]["NAME"] = str(test_name)
            engine = settings.DATABASES["default"].get("ENGINE", "")
            if engine.endswith("sqlite3"):
                db_path = Path(test_name)
                if db_path.exists():
                    db_path.unlink()
            else:
                import psycopg

                params = {
                    "dbname": db_name,
                    "user": settings.DATABASES["default"].get("USER", ""),
                    "password": settings.DATABASES["default"].get("PASSWORD", ""),
                    "host": settings.DATABASES["default"].get("HOST", ""),
                    "port": settings.DATABASES["default"].get("PORT", ""),
                }
                with psycopg.connect(**params) as conn:
                    conn.autocommit = True
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'
                        )
                        cursor.execute(f'CREATE DATABASE "{test_name}"')

        # Perform the regular Django setup after configuring the test database
        _original_setup(*args, **kwargs)

        from django.db import connections

        if test_name:
            connections.databases["default"]["NAME"] = str(test_name)

        # Speed up tests by using a lightweight password hasher and disabling
        # password validation checks. The default PBKDF2 hasher uses one
        # million iterations which is unnecessarily slow for unit tests and
        # would make the migration phase (which creates a default superuser)
        # take several seconds.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
        settings.AUTH_PASSWORD_VALIDATORS = []

        # Apply migrations to create the database schema
        call_command("migrate", run_syncdb=True, verbosity=0)


django.setup = safe_setup
safe_setup()


@dataclass(slots=True)
class StubVenv:
    """Details about the stubbed virtual environment used in shell tests."""

    python: Path
    log: Path


def create_stub_venv(repo: Path) -> StubVenv:
    """Write a fake ``.venv/bin/python`` that captures module invocations.

    The stub records every invocation in ``log`` as a JSON lines file and
    emulates the handful of ``python -m`` commands used by ``env-refresh.sh``
    during the tests. When invoked with ``pip`` or ``ensurepip`` it responds
    immediately without shelling out to a real interpreter so the tests avoid
    slow virtual environment setup.
    """

    venv_dir = repo / ".venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    log_path = venv_dir / "stub-python-log.jsonl"
    python_path = bin_dir / "python"

    script_lines = [
        "#!/usr/bin/env python3",
        "import json",
        "import os",
        "import subprocess",
        "import sys",
        "from pathlib import Path",
        "",
        "LOG_PATH = Path(__LOG_PATH__)",
        "LOG_PATH.parent.mkdir(parents=True, exist_ok=True)",
        "STATE_FILE = LOG_PATH.with_name(\"pip-available.flag\")",
        "",
        "def _write(entry):",
        "    with LOG_PATH.open(\"a\", encoding=\"utf-8\") as handle:",
        "        handle.write(json.dumps(entry, sort_keys=True) + \"\\n\")",
        "",
        "def _should_fail(args):",
        "    pattern = os.environ.get(\"STUB_PYTHON_FAIL_PATTERN\")",
        "    if not pattern:",
        "        return False",
        "    for index, value in enumerate(args):",
        "        if value == \"-r\" and index + 1 < len(args):",
        "            req_path = Path(args[index + 1])",
        "            if req_path.exists():",
        "                try:",
        "                    text = req_path.read_text(encoding=\"utf-8\")",
        "                except OSError:",
        "                    continue",
        "                if pattern in text:",
        "                    return True",
        "        if pattern in value:",
        "            return True",
        "    return False",
        "",
        "def main() -> int:",
        "    argv = sys.argv[1:]",
        "    entry: dict[str, object] = {\"argv\": argv, \"cwd\": os.getcwd()}",
        "    exit_code = 0",
        "    handled = False",
        "    passthrough = False",
        "",
        "    if argv[:1] == [\"-m\"] and len(argv) >= 2:",
        "        module = argv[1]",
        "        entry[\"kind\"] = \"module\"",
        "        entry[\"module\"] = module",
        "        entry[\"args\"] = argv[2:]",
        "        if module == \"pip\":",
        "            handled = True",
        "            if entry[\"args\"][:1] == [\"install\"]:",
        "                if _should_fail(entry[\"args\"][1:]):",
        "                    pattern = os.environ.get(\"STUB_PYTHON_FAIL_PATTERN\", \"package\")",
        "                    sys.stderr.write(f\"ERROR: {pattern} requires a different Python\\n\")",
        "                    exit_code = 1",
        "                else:",
        "                    exit_code = 0",
        "            elif entry[\"args\"][:1] == [\"--version\"]:",
        "                exit_code = 0 if STATE_FILE.exists() else 1",
        "            else:",
        "                exit_code = 0",
        "        elif module == \"ensurepip\":",
        "            handled = True",
        "            try:",
        "                STATE_FILE.write_text(\"\", encoding=\"utf-8\")",
        "            except OSError:",
        "                pass",
        "            exit_code = 0",
        "        else:",
        "            passthrough = True",
        "    elif argv:",
        "        entry[\"kind\"] = \"script\"",
        "        entry[\"script\"] = argv[0]",
        "        entry[\"args\"] = argv[1:]",
        "        name = Path(argv[0]).name",
        "        if name == \"pip_install.py\":",
        "            handled = True",
        "            if _should_fail(entry[\"args\"]):",
        "                pattern = os.environ.get(\"STUB_PYTHON_FAIL_PATTERN\", \"package\")",
        "                sys.stderr.write(f\"ERROR: {pattern} requires a different Python\\n\")",
        "                exit_code = 1",
        "            else:",
        "                exit_code = 0",
        "        elif name.endswith(\".py\"):",
        "            handled = True",
        "            exit_code = 0",
        "        else:",
        "            passthrough = True",
        "    else:",
        "        entry[\"kind\"] = \"noop\"",
        "        handled = True",
        "        exit_code = 0",
        "",
        "    if not handled:",
        "        real = os.environ.get(\"STUB_PYTHON_REAL\")",
        "        passthrough = bool(real)",
        "        if real:",
        "            exit_code = subprocess.call([real, *argv])",
        "        else:",
        "            exit_code = 0",
        "",
        "    entry[\"handled\"] = handled",
        "    entry[\"passthrough\"] = passthrough",
        "    entry[\"exit_code\"] = exit_code",
        "    _write(entry)",
        "    return exit_code",
        "",
        "if __name__ == \"__main__\":",
        "    raise SystemExit(main())",
    ]

    script = "\n".join(script_lines) + "\n"
    script = script.replace("__LOG_PATH__", repr(str(log_path)))

    python_path.write_text(script)
    python_path.chmod(0o755)
    log_path.write_text("")

    state_file = log_path.with_name("pip-available.flag")
    if state_file.exists():
        state_file.unlink()

    return StubVenv(python=python_path, log=log_path)


@pytest.fixture(autouse=True)
def ensure_test_database():
    """Fail fast if tests attempt to use the development database."""
    from django.conf import settings

    name = str(settings.DATABASES["default"]["NAME"])
    if "test" not in name:
        raise RuntimeError("Tests must run against the test database")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "role(name): mark test as associated with a node role"
    )
    config.addinivalue_line(
        "markers", "feature(slug): mark test as requiring a node feature"
    )
    config.addinivalue_line(
        "markers", "django_db: mark test as requiring database access"
    )


def _env_flag(name: str) -> bool:
    """Return ``True`` when the environment flag ``name`` is enabled."""

    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _feature_filter() -> set[str] | None:
    value = os.environ.get("NODE_FEATURES")
    if value is None:
        return None
    features = {item.strip() for item in value.split(",") if item.strip()}
    return features


def _feature_skip_reason(required: set[str], role: str | None) -> str:
    formatted = ", ".join(sorted(required))
    if role:
        return f"{formatted} feature(s) not enabled for {role} role"
    return f"{formatted} feature(s) not enabled for this test run"


def pytest_collection_modifyitems(config, items):
    role = os.environ.get("NODE_ROLE")
    require_markers = _env_flag("NODE_ROLE_ONLY")
    role_skip = pytest.mark.skip(reason=f"not run for {role} role") if role else None
    missing_skip = (
        pytest.mark.skip(reason="missing role marker while NODE_ROLE_ONLY is enabled")
        if require_markers
        else None
    )
    features = _feature_filter()

    for item in items:
        roles = {m.args[0] for m in item.iter_markers("role") if m.args}
        if role and roles and role not in roles:
            item.add_marker(role_skip)
        if require_markers and not roles:
            item.add_marker(missing_skip)
        required = {m.args[0] for m in item.iter_markers("feature") if m.args}
        if features is not None and required and required.isdisjoint(features):
            item.add_marker(
                pytest.mark.skip(reason=_feature_skip_reason(required, role))
            )
