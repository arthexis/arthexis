import importlib
import os

import pytest
from django.test import override_settings


pytestmark = [pytest.mark.feature("celery-queue")]


@pytest.mark.parametrize(
    "role",
    [
        "Watchtower",
        "Constellation",
        "Satellite",
        "Control",
        "Terminal",
        "Gateway",
    ],
)
def test_celery_disables_debug(monkeypatch, role):
    """Celery should not run in debug mode for production node roles."""
    monkeypatch.setenv("NODE_ROLE", role)
    monkeypatch.setenv("CELERY_TRACE_APP", "1")
    # Reload module to apply environment changes
    import config.celery as celery_module

    importlib.reload(celery_module)
    assert "CELERY_TRACE_APP" not in os.environ
    # Cleanup to avoid affecting other tests
    monkeypatch.delenv("NODE_ROLE", raising=False)
    monkeypatch.delenv("CELERY_LOG_LEVEL", raising=False)
    importlib.reload(celery_module)


def test_celery_uses_soft_shutdown_timeout_setting():
    import config.celery as celery_module

    with override_settings(CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT=75):
        importlib.reload(celery_module)
        assert celery_module.app.conf.worker_soft_shutdown_timeout == 75
    importlib.reload(celery_module)
