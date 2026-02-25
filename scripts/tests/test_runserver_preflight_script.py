"""Regression coverage for runserver preflight startup checks."""

from pathlib import Path
import re

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
    assert "run_migrate_check() {" in runserver_preflight_contents
    assert "if run_migrate_check; then" in runserver_preflight_contents
    assert 'if [ "$migrate_check_status" -ne 10 ]; then' in runserver_preflight_contents
    assert re.search(
        r"Checking for unapplied migrations before runserver\.\.\.[\s\S]*"
        r"if run_migrate_check; then[\s\S]*"
        r"Pending migrations detected; applying migrations\.\.\.[\s\S]*"
        r"manage\.py migrate --noinput[\s\S]*"
        r"Verifying migration state after applying migrations\.\.\.[\s\S]*"
        r"manage\.py migrate --check",
        runserver_preflight_contents,
    )


def test_preflight_handles_migrate_check_status_safely(runserver_preflight_contents: str) -> None:
    """Preflight should safely capture migrate --check status under strict shell modes."""
    assert 'if migrate_check_output=$("$python_bin" manage.py migrate --check 2>&1); then' in runserver_preflight_contents
    assert 'migrate_check_status=0' in runserver_preflight_contents
    assert 'migrate_check_status=$?' in runserver_preflight_contents
    assert "grep -Eq" not in runserver_preflight_contents
    assert "unapplied migration|Run 'python manage.py migrate'" not in runserver_preflight_contents


def test_preflight_treats_apply_or_recheck_failures_as_fatal(runserver_preflight_contents: str) -> None:
    """Preflight should return failure when migrate apply or verify commands fail."""
    assert 'if ! "$python_bin" manage.py migrate --noinput; then' in runserver_preflight_contents
    assert 'Migration preflight failed while applying migrations.' in runserver_preflight_contents
    assert 'Migration preflight failed: migrations are still pending after apply.' in runserver_preflight_contents
