"""Regression tests for Release admin top task actions."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.release.admin import PackageReleaseAdmin
from apps.release.models import Package, PackageRelease


def _build_release() -> PackageRelease:
    package = Package.objects.create(name="arthexis-release-test")
    return PackageRelease.objects.create(package=package, version="1.2.3")


@override_settings(BASE_DIR="/tmp/arthexis-release-admin-tests")
def test_release_action_resumes_ongoing_release_process(db) -> None:
    release = _build_release()
    request = RequestFactory().get("/admin/release/packagerelease/")
    request.session = {
        f"release_publish_{release.pk}": {
            "started": True,
            "step": 1,
        }
    }

    admin_view = PackageReleaseAdmin(PackageRelease, admin.site)
    response = admin_view.release_action(request, release)

    assert response.status_code == 302
    assert response.url == f'{reverse("release-progress", args=[release.pk, "publish"])}?resume=1'


def test_release_action_uses_prepare_next_release_when_not_ongoing(db, monkeypatch) -> None:
    release = _build_release()
    request = RequestFactory().get("/admin/release/packagerelease/")
    request.session = {}
    called: dict[str, object] = {}

    def _fake_prepare(admin_view, req, package):
        called["admin_view"] = admin_view
        called["request"] = req
        called["package"] = package
        return HttpResponseRedirect("/prepared/")

    monkeypatch.setattr("apps.release.admin.prepare_package_release", _fake_prepare)

    admin_view = PackageReleaseAdmin(PackageRelease, admin.site)
    response = admin_view.release_action(request, release)

    assert response.status_code == 302
    assert response.url == "/prepared/"
    assert called["admin_view"] is admin_view
    assert called["request"] is request
    assert called["package"] == release.package
