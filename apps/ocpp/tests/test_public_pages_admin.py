from unittest.mock import Mock

import pytest
from django.contrib import admin
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import NoReverseMatch

from apps.ocpp.admin import public_pages as public_pages_admin
from apps.ocpp.admin.public_pages import PublicConnectorPageAdmin
from apps.ocpp.models import Charger, PublicConnectorPage

pytestmark = pytest.mark.django_db


def _disable_public_page_route(monkeypatch):
    monkeypatch.setattr(
        public_pages_admin.PublicConnectorPage,
        "public_url",
        lambda self, request=None: "",
    )


def test_public_connector_page_public_url_is_blank_when_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="PUBLIC-PATH")
    page = PublicConnectorPage.objects.create(charger=charger)
    monkeypatch.setattr(
        "apps.ocpp.models.public_pages.reverse",
        Mock(side_effect=NoReverseMatch),
    )

    assert page.public_path() == ""
    assert page.public_url(rf.get("/")) == ""


def test_public_page_admin_regenerate_warns_when_public_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="PUBLIC-QR")
    page = PublicConnectorPage.objects.create(charger=charger)
    page_admin = PublicConnectorPageAdmin(PublicConnectorPage, admin.site)
    page_admin.message_user = Mock()
    _disable_public_page_route(monkeypatch)

    response = page_admin.regenerate_qr_assets(
        rf.get("/admin/ocpp/publicconnectorpage/"),
        PublicConnectorPage.objects.filter(pk=page.pk),
    )

    assert response.status_code == 302
    page.refresh_from_db()
    assert page.qr_svg == ""
    assert not page.qr_png
    page_admin.message_user.assert_called_once()


def test_public_page_admin_download_qr_warns_when_public_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="PUBLIC-DOWNLOAD")
    page = PublicConnectorPage.objects.create(charger=charger)
    page_admin = PublicConnectorPageAdmin(PublicConnectorPage, admin.site)
    page_admin.message_user = Mock()
    _disable_public_page_route(monkeypatch)

    response = page_admin.download_qr_assets(
        rf.get("/admin/ocpp/publicconnectorpage/"),
        PublicConnectorPage.objects.filter(pk=page.pk),
    )

    assert response.status_code == 302
    page_admin.message_user.assert_called_once()


def test_public_page_admin_download_qr_checks_cached_asset_route(
    rf, monkeypatch, tmp_path
):
    charger = Charger.objects.create(charger_id="PUBLIC-CACHED")
    page = PublicConnectorPage.objects.create(charger=charger)
    page_admin = PublicConnectorPageAdmin(PublicConnectorPage, admin.site)
    page_admin.message_user = Mock()
    _disable_public_page_route(monkeypatch)

    with override_settings(MEDIA_ROOT=tmp_path):
        page.qr_png.save("cached.png", ContentFile(b"cached-png"), save=True)

        response = page_admin.download_qr_assets(
            rf.get("/admin/ocpp/publicconnectorpage/"),
            PublicConnectorPage.objects.filter(pk=page.pk),
        )

    assert response.status_code == 302
    page_admin.message_user.assert_called_once()


def test_public_page_admin_sticker_sheet_warns_when_public_route_is_disabled(
    rf, monkeypatch
):
    charger = Charger.objects.create(charger_id="PUBLIC-STICKER")
    page = PublicConnectorPage.objects.create(charger=charger)
    page_admin = PublicConnectorPageAdmin(PublicConnectorPage, admin.site)
    page_admin.message_user = Mock()
    _disable_public_page_route(monkeypatch)

    response = page_admin.download_sticker_sheet(
        rf.get("/admin/ocpp/publicconnectorpage/"),
        PublicConnectorPage.objects.filter(pk=page.pk),
    )

    assert response.status_code == 302
    page_admin.message_user.assert_called_once()
