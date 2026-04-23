"""Regression tests for Release admin top task actions."""

from __future__ import annotations

from django.contrib import admin
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.release.admin import PackageReleaseAdmin
from apps.release.models import Package, PackageRelease


def _build_release() -> PackageRelease:
    package = Package.objects.create(name="arthexis-release-test")
    return PackageRelease.objects.create(package=package, version="1.2.3")


def test_release_action_resumes_ongoing_release_process(db, tmp_path) -> None:
    with override_settings(BASE_DIR=tmp_path):
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
        assert response.url == (
            f'{reverse("release-progress", args=[release.pk, "publish"])}?resume=1'
        )


def test_release_action_starts_publish_when_not_ongoing(db, tmp_path) -> None:
    with override_settings(BASE_DIR=tmp_path):
        release = _build_release()
        request = RequestFactory().get("/admin/release/packagerelease/")
        request.session = {}

        admin_view = PackageReleaseAdmin(PackageRelease, admin.site)
        response = admin_view.release_action(request, release)

    assert response is not None
    assert response.status_code == 302
    assert response.url == reverse("release-progress", args=[release.pk, "publish"])
