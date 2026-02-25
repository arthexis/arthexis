"""Regression coverage for runserver preflight startup checks."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


RUNSERVER_PREFLIGHT_PATH = Path(__file__).resolve().parents[2] / "scripts/helpers/runserver_preflight.sh"


@pytest.fixture(scope="module")
def runserver_preflight_contents() -> str:
    """Return the runserver preflight shell script contents once per module."""
    return RUNSERVER_PREFLIGHT_PATH.read_text(encoding="utf-8")


def test_preflight_avoids_showmigrations_scan(runserver_preflight_contents: str) -> None:
    """Preflight should avoid expensive migration plan scans on each startup."""
    assert "showmigrations --plan" not in runserver_preflight_contents


def test_preflight_uses_migrate_check_then_apply(runserver_preflight_contents: str) -> None:
    """Preflight should gate migration application behind migrate --check."""
    assert 'if "$python_bin" manage.py migrate --check; then' in runserver_preflight_contents
    assert '"$python_bin" manage.py migrate --noinput' in runserver_preflight_contents
    assert '"$python_bin" manage.py migrate --check' in runserver_preflight_contents


def test_preflight_treats_failed_check_as_fatal(runserver_preflight_contents: str) -> None:
    """Preflight should fail fast when migrate --check errors unexpectedly."""
    assert 'run_migrate_check() {' in runserver_preflight_contents
    assert 'Migration preflight failed while checking migration state:' in runserver_preflight_contents
    assert 'if [ "$migrate_check_status" -ne 10 ]; then' in runserver_preflight_contents
    assert 'return 1' in runserver_preflight_contents


def test_preflight_treats_apply_or_recheck_failures_as_fatal(runserver_preflight_contents: str) -> None:
    """Preflight should return failure when migrate apply or verify commands fail."""
    assert 'if ! "$python_bin" manage.py migrate --noinput; then' in runserver_preflight_contents
    assert 'Migration preflight failed while applying migrations.' in runserver_preflight_contents
    assert 'Migration preflight failed: migrations are still pending after apply.' in runserver_preflight_contents
