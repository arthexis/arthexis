from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.users.admin import (
    MAX_ERROR_REPORT_PACKAGE_SIZE_BYTES,
    UploadedErrorReportUploadForm,
)
from apps.users.models import UploadedErrorReport
from apps.users.tasks import analyze_uploaded_error_report

pytestmark = pytest.mark.django_db


def _zip_upload(name: str = "error-report.zip") -> SimpleUploadedFile:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("summary.txt", "ok")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="application/zip")


def test_uploaded_error_report_admin_requires_add_permission(client):
    user = get_user_model().objects.create_user(
        username="error-report-viewer",
        password="secret",
        is_staff=True,
    )
    client.force_login(user)

    response = client.post(
        reverse("admin:users_uploadederrorreport_upload"),
        data={"source_label": "ops", "package": _zip_upload()},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:index")
    assert UploadedErrorReport.objects.count() == 0


def test_uploaded_error_report_admin_rejects_non_zip_upload(admin_client):
    response = admin_client.post(
        reverse("admin:users_uploadederrorreport_upload"),
        data={
            "source_label": "ops",
            "package": SimpleUploadedFile(
                "error-report.zip",
                b"not a zip",
                content_type="application/zip",
            ),
        },
    )

    assert response.status_code == 200
    assert b"Choose a .zip package to upload." in response.content
    assert UploadedErrorReport.objects.count() == 0


def test_uploaded_error_report_form_rejects_oversized_zip():
    upload = _zip_upload()
    upload.size = MAX_ERROR_REPORT_PACKAGE_SIZE_BYTES + 1
    form = UploadedErrorReportUploadForm(
        data={"source_label": "ops"},
        files={"package": upload},
    )

    assert not form.is_valid()
    assert form.errors["package"] == ["Package exceeds the 25 MB upload limit."]


def test_uploaded_error_report_admin_creates_report_for_valid_zip(admin_client, monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr("apps.users.admin.enqueue_task", lambda *args, **kwargs: True)

    response = admin_client.post(
        reverse("admin:users_uploadederrorreport_upload"),
        data={"source_label": " ops ", "package": _zip_upload()},
    )

    report = UploadedErrorReport.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("admin:users_uploadederrorreport_change", args=[report.pk])
    assert report.source_label == "ops"
    assert report.package.name.endswith(".zip")


def test_uploaded_error_report_changelist_only_links_custom_upload(admin_client):
    response = admin_client.get(reverse("admin:users_uploadederrorreport_changelist"))

    assert response.status_code == 200
    assert b"Upload error report" in response.content
    assert b"Add uploaded error report" not in response.content


def test_uploaded_error_report_admin_marks_failed_when_sync_fallback_raises(
    admin_client,
    monkeypatch,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr("apps.users.admin.enqueue_task", lambda *args, **kwargs: False)

    def fail_analysis(report_id: int) -> None:
        raise RuntimeError("analysis unavailable")

    monkeypatch.setattr("apps.users.admin.analyze_uploaded_error_report", fail_analysis)

    response = admin_client.post(
        reverse("admin:users_uploadederrorreport_upload"),
        data={"source_label": "ops", "package": _zip_upload()},
    )

    report = UploadedErrorReport.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("admin:users_uploadederrorreport_change", args=[report.pk])
    assert report.status == UploadedErrorReport.Status.FAILED
    assert report.error == "analysis unavailable"


def test_uploaded_error_report_change_page_refreshes_only_while_processing(admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    report = UploadedErrorReport.objects.create(
        package=_zip_upload(),
        status=UploadedErrorReport.Status.COMPLETE,
    )

    response = admin_client.get(reverse("admin:users_uploadederrorreport_change", args=[report.pk]))

    assert response.status_code == 200
    assert b'http-equiv="refresh"' not in response.content
    assert b'name="package"' not in response.content

    report.status = UploadedErrorReport.Status.PROCESSING
    report.save(update_fields=["status"])
    response = admin_client.get(reverse("admin:users_uploadederrorreport_change", args=[report.pk]))

    assert response.status_code == 200
    assert b'http-equiv="refresh"' in response.content


def test_analyze_uploaded_error_report_marks_known_analysis_errors_failed(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    report = UploadedErrorReport.objects.create(package=_zip_upload())
    seen_paths = []

    def fail_analysis(path):
        seen_paths.append(path)
        raise ValueError("Malformed error-report package")

    monkeypatch.setattr(
        "apps.users.tasks.analyze_error_report_package",
        fail_analysis,
    )

    analyze_uploaded_error_report(report.pk)

    assert seen_paths
    assert not isinstance(seen_paths[0], str)
    report.refresh_from_db()
    assert report.status == UploadedErrorReport.Status.FAILED
    assert report.error == "Malformed error-report package"


def test_analyze_uploaded_error_report_marks_unexpected_analysis_errors_failed(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    report = UploadedErrorReport.objects.create(package=_zip_upload())
    monkeypatch.setattr(
        "apps.users.tasks.analyze_error_report_package",
        lambda path: (_ for _ in ()).throw(RuntimeError("parser exploded")),
    )

    with pytest.raises(RuntimeError, match="parser exploded"):
        analyze_uploaded_error_report(report.pk)

    report.refresh_from_db()
    assert report.status == UploadedErrorReport.Status.FAILED
    assert report.error == "parser exploded"
