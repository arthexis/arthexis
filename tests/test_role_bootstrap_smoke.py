"""Regression smoke tests for role-specific Django and Celery bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


pytestmark = [pytest.mark.critical, pytest.mark.pr_origin(6177)]


_ROLE_TEST_CASES = ("Terminal", "Control", "Satellite", "Watchtower")
_ROLE_SPECIFIC_SETTINGS = ("BROKER_URL", "CELERY_BROKER_URL", "CHANNEL_REDIS_URL", "OCPP_STATE_REDIS_URL")


def _run_role_bootstrap(*, node_role: str) -> subprocess.CompletedProcess[str]:
    """Run a clean subprocess that imports settings and Celery for a specific node role.

    Parameters:
        node_role: NODE_ROLE value to test.

    Returns:
        CompletedProcess output from the subprocess run.

    Raises:
        pytest.fail: If the subprocess times out.
    """

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


@pytest.mark.parametrize("node_role", _ROLE_TEST_CASES)
def test_role_bootstrap_imports_succeed_without_role_specific_environment(node_role: str) -> None:
    """Supported roles should bootstrap without requiring role-specific runtime settings.

    Parameters:
        node_role: NODE_ROLE value under test.

    Returns:
        None

    Raises:
        AssertionError: If process import/bootstrap fails for the role.
    """

    result = _run_role_bootstrap(node_role=node_role)

    assert result.returncode == 0, (
        f"{node_role} bootstrap failed with rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
