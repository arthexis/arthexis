from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.sites.admin.reports_admin import log_viewer


pytestmark = [pytest.mark.django_db]


def test_log_viewer_per_file_table_links_to_log_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    logs_dir = Path(settings.BASE_DIR) / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_name = f"test-log-viewer clickable {uuid4().hex}.log"
    log_path = logs_dir / log_name

    try:
        log_path.write_text("[INFO] boot ok\n", encoding="utf-8")

        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            username=f"log-viewer-staff-{uuid4().hex[:8]}",
            email="log-viewer-staff@example.com",
            password="secret",
            is_staff=True,
            is_superuser=True,
        )

        rf = RequestFactory()
        url = reverse("admin:log_viewer")

        request = rf.get(url)
        request.user = staff_user
        response = log_viewer(request)
        response.render()
        body = response.content.decode()

        assert response.status_code == 200
        assert f'?log={quote(log_name)}' in body

        selected_request = rf.get(url, {"log": log_name})
        selected_request.user = staff_user
        selected_response = log_viewer(selected_request)
        selected_response.render()
        selected_body = selected_response.content.decode()

        assert selected_response.status_code == 200
        assert f"Viewing {log_name}" in selected_body
    finally:
        log_path.unlink(missing_ok=True)
