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


def test_is_safe_diagnostics_location_rejects_private_addresses(monkeypatch):
    """Diagnostics downloads should reject private and loopback destinations."""
    admin = ChargerAdmin(Charger, AdminSite())

    monkeypatch.setattr(
        "apps.ocpp.admin.charger.diagnostics.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("127.0.0.1", 0)),
        ],
    )

    assert admin._is_safe_diagnostics_location("https://127.0.0.1/file.log") is False
    assert admin._is_safe_diagnostics_location("https://localhost/file.log") is False


def test_is_safe_diagnostics_location_accepts_public_ip():
    """Diagnostics downloads should allow public IP destinations."""
    admin = ChargerAdmin(Charger, AdminSite())

    assert admin._is_safe_diagnostics_location("https://8.8.8.8/file.log") is True


def test_download_diagnostics_rejects_unsafe_redirect(monkeypatch, tmp_path):
    """Diagnostics downloads should reject redirects to unsafe destinations."""
    admin = ChargerAdmin(Charger, AdminSite())

    class Response:
        def __init__(self):
            self.status_code = 302
            self.headers = {"Location": "http://127.0.0.1/internal"}
            self.is_redirect = True

        def close(self):
            return None

    monkeypatch.setattr(
        "apps.ocpp.admin.charger.diagnostics.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        admin,
        "_is_safe_diagnostics_location",
        lambda location: location == "https://example.com/diagnostics.log",
    )

    with pytest.raises(admin.DiagnosticsDownloadError, match="redirect location host is not allowed"):
        admin._download_diagnostics(
            request=None,
            charger=object(),
            location="https://example.com/diagnostics.log",
            diagnostics_dir=tmp_path,
            user_dir=tmp_path,
        )
