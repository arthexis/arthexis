from pathlib import Path

import pytest
from django.conf import settings

from apps.content.utils import save_screenshot

pytestmark = pytest.mark.django_db


def test_save_screenshot_preserves_long_method_label(monkeypatch):
    monkeypatch.setattr(
        "apps.content.classifiers.registry.run_default_classifiers", lambda sample: []
    )
    screenshot_path = Path(settings.LOG_DIR) / "screenshots" / "long-method.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.write_bytes(b"fake screenshot bytes")

    method = "TEST:OCPP Dashboard"

    sample = save_screenshot(
        screenshot_path,
        method=method,
        link_duplicates=True,
    )

    assert sample is not None
    assert sample.method == method
