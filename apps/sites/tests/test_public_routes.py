import datetime
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

from apps.energy.models import ClientReport
from apps.features.models import Feature
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
from apps.sites.favicons import (
    FAVICON_CONTENT_TYPE,
    FAVICON_FILENAMES,
    load_favicon_bytes,
)
from apps.sites.models import Landing, SiteProfile
from apps.sites.utils import require_site_operator_or_staff
from apps.sites.views import landing

pytestmark = [pytest.mark.django_db]


def _grant_docs_access(user):
    release_manager_group, _ = SecurityGroup.objects.get_or_create(
        name=RELEASE_MANAGER_GROUP_NAME
    )
    release_manager_group.user_set.add(user)


def test_favicon_route_serves_default_suite_icon(client, monkeypatch):
    monkeypatch.setattr(landing, "get_site", lambda _request: None)
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_ignores_self_relative_site_favicon(client, monkeypatch):
    monkeypatch.setattr(
        landing, "_get_site_favicon_url", lambda _request: "/favicon.ico"
    )
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_ignores_self_bare_relative_site_favicon(client, monkeypatch):
    monkeypatch.setattr(landing, "_get_site_favicon_url", lambda _request: "favicon.ico")
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_ignores_self_absolute_site_favicon(client, monkeypatch):
    monkeypatch.setattr(
        landing, "_get_site_favicon_url", lambda _request: "http://testserver/favicon.ico"
    )
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_ignores_self_absolute_http_default_port(client, monkeypatch):
    monkeypatch.setattr(
        landing,
        "_get_site_favicon_url",
        lambda _request: "http://testserver:80/favicon.ico",
    )
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_ignores_self_absolute_https_default_port(client, monkeypatch):
    monkeypatch.setattr(
        landing,
        "_get_site_favicon_url",
        lambda _request: "https://testserver:443/favicon.ico",
    )
    monkeypatch.setattr(landing.Node, "get_local", lambda: SimpleNamespace(role=None))

    response = client.get("/favicon.ico", secure=True)

    assert response.status_code == 200
    assert response["Content-Type"] == FAVICON_CONTENT_TYPE
    assert response.content == load_favicon_bytes(FAVICON_FILENAMES["default"])


def test_favicon_route_redirects_to_distinct_site_favicon(client, monkeypatch):
    monkeypatch.setattr(
        landing, "_get_site_favicon_url", lambda _request: "/media/favicon.ico"
    )

    response = client.get("/favicon.ico")

    assert response.status_code == 302
    assert response["Location"] == "/media/favicon.ico"


def test_get_role_favicon_filename_falls_back_when_role_relation_is_missing(
    monkeypatch,
):
    class NodeWithMissingRole:
        @property
        def role(self):
            raise landing.ObjectDoesNotExist("role missing")

    monkeypatch.setattr(landing.Node, "get_local", lambda: NodeWithMissingRole())

    assert landing._get_role_favicon_filename() == FAVICON_FILENAMES["default"]


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
