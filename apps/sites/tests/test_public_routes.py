import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.docs.models import DocumentIndex, DocumentIndexAssignment
from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.gallery.models import GalleryImage
from apps.groups.constants import (
    AP_USER_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
    SITE_OPERATOR_GROUP_NAME,
)
from apps.groups.models import SecurityGroup
from apps.media.utils import create_media_file, ensure_media_bucket
from apps.modules.models import Module
from apps.repos.models.response_templates import GitHubResponseTemplate
from apps.repos.services.github import GitHubRepositoryError
from apps.sites import context_processors
from apps.sites.models import Landing, SiteProfile
from apps.sites.utils import require_site_operator_or_staff

pytestmark = [pytest.mark.django_db]

def _grant_docs_access(user):
    release_manager_group, _ = SecurityGroup.objects.get_or_create(
        name=RELEASE_MANAGER_GROUP_NAME
    )
    release_manager_group.user_set.add(user)

def test_client_report_download_enforces_login_and_ownership(client, monkeypatch, tmp_path):
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="report-owner", email="owner@example.com", password="secret"
    )
    other_user = user_model.objects.create_user(
        username="report-other", email="other@example.com", password="secret"
    )
    staff_user = user_model.objects.create_user(
        username="route-staff",
        email="route-staff@example.com",
        password="secret",
        is_staff=True,
    )
    report = ClientReport.objects.create(
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 1, 31),
        owner=owner,
        data={},
    )
    download_url = reverse("pages:client-report-download", args=[report.pk])

    assert client.get(download_url).status_code == 302

    client.force_login(other_user)
    assert client.get(download_url, follow=True).status_code == 403

    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n%EOF")
    monkeypatch.setattr(ClientReport, "ensure_pdf", lambda self: Path(pdf_file))

    client.force_login(owner)
    owner_response = client.get(download_url, follow=True)
    assert owner_response.status_code == 200
    assert owner_response["Content-Type"] == "application/pdf"

    client.force_login(staff_user)
    staff_response = client.get(download_url, follow=True)
    assert staff_response.status_code == 200
    assert staff_response["Content-Type"] == "application/pdf"
@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/en//evil.com", 404),
        ("/en///evil.com", 404),
        (r"/en/\evil.com", 301),
    ],
)
def test_legacy_language_redirect_rejects_scheme_relative_targets(
    client, path, expected_status
):
    response = client.get(path, follow=False)

    assert response.status_code == expected_status
    if response.status_code in {301, 302, 307, 308}:
        assert not response["Location"].startswith("//")
def test_docs_library_shows_gallery_sidebar_with_latest_four_images(client):
    user = get_user_model().objects.create_user(
        username="docs-gallery-sidebar-user",
        email="docs-gallery-sidebar-user@example.com",
        password="secret",
    )
    developer_group, _ = SecurityGroup.objects.get_or_create(
        name=PRODUCT_DEVELOPER_GROUP_NAME
    )
    developer_group.user_set.add(user)
    bucket = ensure_media_bucket(slug="gallery-images", name="Gallery Images")
    for index in range(5):
        upload = SimpleUploadedFile(
            f"gallery-{index}.png",
            b"\x89PNG\r\n\x1a\n",
            content_type="image/png",
        )
        media_file = create_media_file(bucket=bucket, uploaded_file=upload)
        GalleryImage.objects.create(
            media_file=media_file,
            title=f"Gallery image {index}",
            include_in_public_gallery=True,
            owner_user=user,
        )

    client.force_login(user)
    response = client.get(reverse("docs:docs-library"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Developer Gallery" in body
    assert reverse("gallery:index") in body
    assert reverse("gallery:upload") in body
    assert "Gallery image 4" in body
    assert "Gallery image 3" in body
    assert "Gallery image 2" in body
    assert "Gallery image 1" in body
    assert "Gallery image 0" not in body

def test_readme_resolves_sigils_for_authenticated_user(client):
    user = get_user_model().objects.create_user(
        username="docs-sigil-user",
        email="docs-sigil-user@example.com",
        password="secret",
    )
    document = Path("docs/sigil-test.md")
    document.write_text("# Sigils\n\nCurrent path: [REQ.path]\n", encoding="utf-8")
    _grant_docs_access(user)
    client.force_login(user)
    try:
        response = client.get(reverse("docs:docs-document", args=["sigil-test.md"]))
        assert response.status_code == 200
        assert "Current path: /docs/sigil-test.md" in response.content.decode()
    finally:
        document.unlink(missing_ok=True)

def test_docs_library_virtual_root_does_not_collide_with_real_root_named_folder(client):
    user = get_user_model().objects.create_user(
        username="docs-root-collision-user",
        email="docs-root-collision-user@example.com",
        password="secret",
        is_staff=True,
    )
    nested_document = Path("docs/__root__/folder-navigation-test.md")
    nested_document.parent.mkdir(parents=True, exist_ok=True)
    nested_document.write_text("# Root folder\n\nFolder document.\n", encoding="utf-8")
    _grant_docs_access(user)

    client.force_login(user)
    try:
        cache.clear()
        folder_response = client.get(
            reverse("docs:docs-library"), {"docs_path": "__root__"}
        )

        assert folder_response.status_code == 200
        assert "folder-navigation-test.md" in folder_response.content.decode()
    finally:
        nested_document.unlink(missing_ok=True)
        if nested_document.parent.exists():
            nested_document.parent.rmdir()

def test_docs_library_folder_view_includes_file_matching_prefix_exactly(client):
    user = get_user_model().objects.create_user(
        username="docs-prefix-file-user",
        email="docs-prefix-file-user@example.com",
        password="secret",
        is_staff=True,
    )
    prefixed_document = Path("docs/library-prefix-match.md")
    prefixed_document.write_text("# Prefix match\n\nExact path document.\n", encoding="utf-8")
    _grant_docs_access(user)

    client.force_login(user)
    try:
        cache.clear()
        response = client.get(
            reverse("docs:docs-library"), {"docs_path": "library-prefix-match.md"}
        )

        assert response.status_code == 200
        assert "library-prefix-match.md" in response.content.decode()
    finally:
        prefixed_document.unlink(missing_ok=True)

def test_docs_library_folder_entries_include_content_blurbs(client):
    user = get_user_model().objects.create_user(
        username="docs-folder-blurb-user",
        email="docs-folder-blurb-user@example.com",
        password="secret",
        is_staff=True,
    )
    first_document = Path("docs/library-blurb-test/alpha.md")
    second_document = Path("docs/library-blurb-test/beta.md")
    first_document.parent.mkdir(parents=True, exist_ok=True)
    first_document.write_text("# Alpha\n\nFolder blurb alpha.\n", encoding="utf-8")
    second_document.write_text("# Beta\n\nFolder blurb beta.\n", encoding="utf-8")
    _grant_docs_access(user)

    client.force_login(user)
    try:
        cache.clear()
        response = client.get(reverse("docs:docs-library"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "library-blurb-test/" in body
        assert "2 docs: alpha.md, beta.md." in body
    finally:
        first_document.unlink(missing_ok=True)
        second_document.unlink(missing_ok=True)
        if first_document.parent.exists():
            first_document.parent.rmdir()
        cache.clear()

def test_docs_library_folder_blurb_ignores_index_only_nested_folders(client):
    user = get_user_model().objects.create_user(
        username="docs-folder-blurb-index-user",
        email="docs-folder-blurb-index-user@example.com",
        password="secret",
        is_staff=True,
    )
    parent_document = Path("docs/library-blurb-parent/direct.md")
    nested_index_document = Path("docs/library-blurb-parent/child/index.md")
    parent_document.parent.mkdir(parents=True, exist_ok=True)
    nested_index_document.parent.mkdir(parents=True, exist_ok=True)
    parent_document.write_text("# Direct\n\nTop-level document.\n", encoding="utf-8")
    nested_index_document.write_text("# Index\n\nHidden nested index.\n", encoding="utf-8")
    _grant_docs_access(user)

    client.force_login(user)
    try:
        cache.clear()
        response = client.get(reverse("docs:docs-library"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "library-blurb-parent/" in body
        assert "1 doc: direct.md." in body
        assert "nested folder with additional documentation" not in body
    finally:
        nested_index_document.unlink(missing_ok=True)
        parent_document.unlink(missing_ok=True)
        if nested_index_document.parent.exists():
            nested_index_document.parent.rmdir()
        if parent_document.parent.exists():
            parent_document.parent.rmdir()
        cache.clear()
