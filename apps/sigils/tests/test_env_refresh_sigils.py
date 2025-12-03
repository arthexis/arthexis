import importlib.util
from pathlib import Path

import pytest
from django.conf import settings


@pytest.fixture()
def env_refresh_module():
    path = Path(settings.BASE_DIR) / "env-refresh.py"
    spec = importlib.util.spec_from_file_location("env_refresh", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sigilroot_skip_reason_missing_app(env_refresh_module):
    reason = env_refresh_module._sigilroot_skip_reason(
        {"content_type": ["noapp", "model"]}
    )

    assert reason == "missing app 'noapp'"


def test_sigilroot_skip_reason_missing_model(env_refresh_module):
    reason = env_refresh_module._sigilroot_skip_reason(
        {"content_type": ["pages", "nosuchmodel"]}
    )

    assert reason == "missing model 'pages.nosuchmodel'"


def test_sigilroot_skip_reason_valid_model(env_refresh_module):
    reason = env_refresh_module._sigilroot_skip_reason(
        {"content_type": ["contenttypes", "contenttype"]}
    )

    assert reason is None
