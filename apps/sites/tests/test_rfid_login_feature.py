"""Tests for RFID/NFC login visibility and access controls."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.features.models import Feature

pytestmark = [pytest.mark.django_db]


def _set_nfc_login_feature(enabled: bool) -> None:
    Feature.objects.update_or_create(
        slug="nfc-login",
        defaults={
            "display": "NFC Login",
            "is_enabled": enabled,
        },
    )


def test_login_page_hides_rfid_link_without_rfid_or_nfc_feature(client, monkeypatch):
    """Login page should hide RFID CTA when neither gate is enabled."""

    _set_nfc_login_feature(False)
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:login"))
    assert response.status_code == 200
    assert response.context["show_rfid_login"] is False
    assert response.context["show_rfid_login_when_nfc_available"] is False


def test_login_page_shows_nfc_conditioned_rfid_link_when_feature_enabled(client, monkeypatch):
    """NFC suite feature should expose RFID CTA with client NFC probe behavior."""

    _set_nfc_login_feature(True)
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:login"))
    assert response.status_code == 200
    assert response.context["show_rfid_login"] is True
    assert response.context["show_rfid_login_when_nfc_available"] is True


def test_login_page_hides_default_footer(client, monkeypatch):
    """Public login page should suppress footer placeholder rendering."""

    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:login"))
    assert response.status_code == 200
    assert response.context["hide_default_footer"] is True


def test_rfid_login_page_allows_nfc_feature_without_node_scanner(client, monkeypatch):
    """NFC suite feature should allow RFID login page without node scanner features."""

    _set_nfc_login_feature(True)
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:rfid-login"))
    assert response.status_code == 200


def test_rfid_login_page_omits_scan_url_without_rfid_scanner(client, monkeypatch):
    """NFC-only RFID login page should not poll scanner endpoint when scanner is absent."""

    _set_nfc_login_feature(True)
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:rfid-login"))
    assert response.status_code == 200
    assert response.context["scan_api_url"] == ""


def test_rfid_login_page_requires_rfid_or_nfc_feature(client, monkeypatch):
    """RFID login page should still 404 when both RFID and NFC gates are disabled."""

    _set_nfc_login_feature(False)
    monkeypatch.setattr("apps.sites.views.management.Node.get_local", lambda: None)
    response = client.get(reverse("pages:rfid-login"))
    assert response.status_code == 404
