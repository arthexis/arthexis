"""Regression smoke tests for role-specific Django and Celery bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


pytestmark = [pytest.mark.critical, pytest.mark.regression]


_ROLE_TEST_CASES = [
    ("Terminal", {}),
    ("Control", {"CELERY_BROKER_URL": "redis://localhost:6379/0"}),
    ("Satellite", {"OCPP_STATE_REDIS_URL": "redis://localhost:6379/1"}),
    ("Watchtower", {"CHANNEL_REDIS_URL": "redis://localhost:6379/2"}),
]

_ROLE_SPECIFIC_SETTINGS = tuple(sorted({key for _, extra_env in _ROLE_TEST_CASES for key in extra_env}))


def _run_role_bootstrap(*, node_role: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a clean subprocess that imports settings and Celery for a specific node role."""

    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH"}
    }
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DEBUG": "0",
            "NODE_ROLE": node_role,
        }
    )

    for setting_name in _ROLE_SPECIFIC_SETTINGS:
        env.pop(setting_name, None)

    if extra_env:
        env.update(extra_env)

    try:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib; importlib.import_module('config.settings'); importlib.import_module('config.celery')",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"{node_role} bootstrap timed out after 30s\n"
            f"stdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        )


@pytest.mark.parametrize(
    ("node_role", "extra_env"),
    _ROLE_TEST_CASES,
)
def test_role_bootstrap_imports_succeed_with_minimum_required_environment(
    node_role: str, extra_env: dict[str, str]
) -> None:
    """Each supported role can import settings and Celery with minimal valid environment values."""

    result = _run_role_bootstrap(node_role=node_role, extra_env=extra_env)

    assert result.returncode == 0, (
        f"{node_role} bootstrap failed with rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
