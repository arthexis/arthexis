import pytest
from django.contrib.admin.sites import AdminSite

from apps.ocpp.admin.charger import ChargerAdmin
from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_content_disposition_filename_utf8():
    """The diagnostics filename parser should decode RFC5987 filenames."""
    admin = ChargerAdmin(Charger, AdminSite())

    value = "attachment; filename*=UTF-8''diagnostics%20report.log"

    assert admin._content_disposition_filename(value) == "diagnostics report.log"


def test_get_urls_contains_view_in_site_route():
    """Custom admin URL should remain registered from centralized get_urls."""
    admin = ChargerAdmin(Charger, AdminSite())

    names = {url.name for url in admin.get_urls()}

    assert "ocpp_charger_view_charge_point_dashboard" in names
