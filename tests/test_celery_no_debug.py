import importlib
import os

import pytest


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
