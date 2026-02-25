"""Regression tests for admin log viewer rendering."""

from pathlib import Path

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.regression
@pytest.mark.integration
def test_admin_log_viewer_displays_full_log_without_slider(admin_client, settings, tmp_path):
    """The admin log viewer should render complete log content without a range slider."""

    settings.BASE_DIR = tmp_path
    logs_dir = Path(tmp_path) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_name = "service-start.log"
    log_path = logs_dir / log_name
    expected_lines = ["line-1", "line-2", "line-3", "line-4"]
    log_path.write_text("\n".join(expected_lines) + "\n", encoding="utf-8")

    response = admin_client.get(reverse("admin:log_viewer"), {"log": log_name})

    assert response.status_code == 200
    content = response.content.decode()
    assert 'type="range"' not in content
    assert "line-1" in content
    assert "line-4" in content
    assert "Last updated" in content
    assert str(log_path.resolve()) in content
    assert "log-viewer-copy-button" in content


@pytest.mark.django_db
@pytest.mark.regression
@pytest.mark.integration
def test_admin_dashboard_uses_short_logs_label(admin_client):
    """The admin dashboard quick action should label the log viewer button as Logs."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert ">Logs<" in content
    assert "Log Viewer" not in content
