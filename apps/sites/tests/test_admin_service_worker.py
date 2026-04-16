import pytest
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse

pytestmark = [pytest.mark.django_db]


def test_admin_base_template_registers_service_worker(client):
    response = client.get(reverse("admin:login"))

    assert response.status_code == 200
    response_text = response.content.decode()
    assert reverse("admin-service-worker") in response_text
    assert staticfiles_storage.url("core/vendor/chart.umd.min.js") in response_text
    assert staticfiles_storage.url("htmx/htmx.min.js") in response_text
    assert "const precacheUrls = [" in response_text
    assert "?precache=${precacheParam}" in response_text


def test_admin_service_worker_script_imports_static_worker(client):
    response = client.get(reverse("admin-service-worker"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    body = response.content.decode()
    assert "importScripts(" in body
    assert staticfiles_storage.url("pages/js/admin-sw.js") in body


def test_admin_service_worker_does_not_cache_admin_html():
    try:
        with staticfiles_storage.open("pages/js/admin-sw.js") as service_worker_file:
            body = service_worker_file.read().decode()
    except FileNotFoundError:
        resolved_path = finders.find("pages/js/admin-sw.js")
        assert resolved_path
        with open(resolved_path, encoding="utf-8") as service_worker_file:
            body = service_worker_file.read()

    assert "ADMIN_DOCUMENT_CACHE_NAME" not in body
    assert "isAdminDocumentRequest" not in body
