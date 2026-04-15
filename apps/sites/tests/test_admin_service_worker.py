import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse

pytestmark = [pytest.mark.django_db]


def test_admin_base_template_registers_service_worker(client):
    response = client.get(reverse("admin:login"))

    assert response.status_code == 200
    response_text = response.content.decode()
    assert staticfiles_storage.url("pages/js/admin-sw.js") in response_text
    assert staticfiles_storage.url("core/vendor/chart.umd.min.js") in response_text
    assert staticfiles_storage.url("htmx/htmx.min.js") in response_text
    assert "window.__ARTHEXIS_ADMIN_SW_PRECACHE_URLS" in response_text
