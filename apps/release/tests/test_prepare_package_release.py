from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from apps.release.admin import package_actions
from apps.release.models import Package, PackageRelease


class DummyResponse:
    def __init__(
        self,
        releases: dict[str, list[object]],
        ok: bool = True,
        status_code: int = 200,
    ):
        self.ok = ok
        self.status_code = status_code
        self._releases = releases

    def json(self) -> dict[str, object]:
        return {"releases": self._releases}

    def close(self) -> None:
        return None


@pytest.mark.django_db
def test_prepare_package_release_get_redirects_to_existing_draft(monkeypatch):
    package = Package.objects.create(name="test-package")
    release = PackageRelease.all_objects.create(
        package=package,
        version="1.0.0",
        is_deleted=True,
    )
    request = RequestFactory().get("/admin/release/package/prepare-next-release/")
    request.user = SimpleNamespace(is_active=True, is_staff=True)
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    def fake_get(url: str, timeout: int = 10) -> DummyResponse:
        return DummyResponse({"1.0.0": []})

    monkeypatch.setattr(package_actions.requests, "get", fake_get)

    admin_view = SimpleNamespace(admin_site=AdminSite(), message_user=lambda *args: None)

    response = package_actions.prepare_package_release(admin_view, request, package)

    assert response.url == reverse(
        "admin:release_packagerelease_change", args=[release.pk]
    )
    release.refresh_from_db()
    assert release.is_deleted is False


@pytest.mark.django_db
def test_prepare_package_release_warns_when_pypi_unavailable(monkeypatch):
    package = Package.objects.create(name="test-package")
    request = RequestFactory().get("/admin/release/package/prepare-next-release/")
    request.META["HTTP_REFERER"] = "/admin/release/packagerelease/"
    request.user = SimpleNamespace(is_active=True, is_staff=True)
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    def fake_get(url: str, timeout: int = 10) -> DummyResponse:
        raise requests.RequestException("boom")

    monkeypatch.setattr(package_actions.requests, "get", fake_get)

    admin_view = SimpleNamespace(admin_site=AdminSite(), message_user=lambda *args: None)

    response = package_actions.prepare_package_release(admin_view, request, package)

    assert response.url == "/admin/release/packagerelease/"


@pytest.mark.django_db
def test_prepare_package_release_skips_draft_when_pypi_ahead(monkeypatch):
    package = Package.objects.create(name="test-package")
    PackageRelease.all_objects.create(
        package=package,
        version="1.0.0",
    )

    def fake_get(url: str, timeout: int = 10) -> DummyResponse:
        return DummyResponse({"1.0.1": []})

    monkeypatch.setattr(package_actions.requests, "get", fake_get)

    request = RequestFactory().post("/admin/release/package/prepare-next-release/")
    request.user = SimpleNamespace(is_active=True, is_staff=True)
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))
    admin_view = SimpleNamespace(admin_site=AdminSite(), message_user=lambda *args: None)
    response = package_actions.prepare_package_release(admin_view, request, package)

    new_release = PackageRelease.objects.get(package=package, version="1.0.2")
    assert response.url == reverse(
        "admin:release_packagerelease_change", args=[new_release.pk]
    )


@pytest.mark.django_db
def test_prepare_package_release_handles_invalid_versions(monkeypatch):
    package = Package.objects.create(name="test-package")
    PackageRelease.all_objects.create(
        package=package,
        version="not-a-version",
    )

    def fake_get(url: str, timeout: int = 10) -> DummyResponse:
        return DummyResponse({"not.valid": [], "2.0.0": []})

    monkeypatch.setattr(package_actions.requests, "get", fake_get)

    request = RequestFactory().post("/admin/release/package/prepare-next-release/")
    request.user = SimpleNamespace(is_active=True, is_staff=True)
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))
    admin_view = SimpleNamespace(admin_site=AdminSite(), message_user=lambda *args: None)
    response = package_actions.prepare_package_release(admin_view, request, package)

    new_release = PackageRelease.objects.get(package=package, version="2.0.1")
    assert response.url == reverse(
        "admin:release_packagerelease_change", args=[new_release.pk]
    )
