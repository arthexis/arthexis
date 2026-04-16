import datetime
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.sites.models import Site
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.docs.models import DocumentIndex, DocumentIndexAssignment
from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.groups.constants import (
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
    SITE_OPERATOR_GROUP_NAME,
)
from apps.groups.models import SecurityGroup
from apps.modules.models import Module
from apps.sites import context_processors
from apps.sites.models import Landing, SiteProfile
from apps.sites.utils import require_site_operator_or_staff

pytestmark = [pytest.mark.django_db]

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

def test_invitation_login_invalid_tokens_are_handled_safely(client):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="invite-user", email="invite-user@example.com", password="secret"
    )
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    invalid_token_response = client.get(
        reverse("pages:invitation-login", args=[uid, "bad-token"]), follow=True
    )
    assert invalid_token_response.status_code == 400
    assert "Invalid invitation link" in invalid_token_response.content.decode()

    malformed_uid_response = client.get(
        reverse("pages:invitation-login", args=["!!invalid!!", "bad-token"]),
        follow=True,
    )
    assert malformed_uid_response.status_code == 400

@pytest.mark.integration
def test_whatsapp_webhook_requires_post_and_feature_flag(client, settings):
    url = reverse("pages:whatsapp-webhook")

    assert client.get(url).status_code == 405
    prefixed = client.get(f"/en{url}", follow=False)
    assert prefixed.status_code == 301
    assert prefixed["Location"] == url

    settings.PAGES_WHATSAPP_ENABLED = False
    disabled = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert disabled.status_code == 404

@pytest.mark.integration
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

@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"from": "+15551234", "message": "Hello"}, 201),
        ("{not-json}", 400),
        ({"from": "", "message": ""}, 400),
    ],
)
def test_whatsapp_webhook_post_payload_validation(
    client, settings, payload, expected_status
):
    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")
    response = client.post(
        url,
        data=payload if isinstance(payload, str) else json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json()["status"] == "ok"

@pytest.mark.critical
def test_require_site_operator_or_staff_enforces_admin_operator_boundary(rf):
    request = rf.get("/ocpp/secure/")
    user_model = get_user_model()
    regular_user = user_model.objects.create_user(
        username="boundary-regular",
        email="boundary-regular@example.com",
        password="secret",
    )
    request.user = regular_user

    with pytest.raises(PermissionDenied):
        require_site_operator_or_staff(request)

    operator_user = user_model.objects.create_user(
        username="boundary-operator",
        email="boundary-operator@example.com",
        password="secret",
    )
    Group.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)[0].user_set.add(
        operator_user
    )
    request.user = operator_user
    assert require_site_operator_or_staff(request) is None


def test_public_charge_point_dashboard_redirects_anonymous_users_to_login(client):
    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('pages:login')}?next=")


@pytest.mark.parametrize("path_name", ["ocpp:ocpp-dashboard", "ocpp:cp-simulator"])
def test_charge_point_views_forbid_authenticated_non_operator_users(client, path_name):
    user = get_user_model().objects.create_user(
        username=f"charge-point-regular-{path_name.split(':')[-1]}",
        email=f"{path_name.split(':')[-1]}@example.com",
        password="secret",
    )
    client.force_login(user)

    response = client.get(reverse(path_name))

    assert response.status_code == 403


def test_charge_points_module_hides_dashboard_and_simulator_links_from_anonymous_users():
    module = Module.objects.create(path="/charge-points/", menu="Charge Points")
    Landing.objects.create(
        module=module,
        path=reverse("ocpp:ocpp-dashboard"),
        label="Charging Station Dashboards",
    )
    Landing.objects.create(
        module=module,
        path=reverse("ocpp:cp-simulator"),
        label="EVCS Online Simulator",
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]
    assert not any(module.path == "/charge-points/" for module in nav_modules)

def test_charge_points_module_shows_dashboard_and_simulator_links_to_site_operators():
    module = Module.objects.create(path="/charge-points/", menu="Charge Points")
    Landing.objects.create(
        module=module,
        path=reverse("ocpp:ocpp-dashboard"),
        label="Charging Station Dashboards",
    )
    Landing.objects.create(
        module=module,
        path=reverse("ocpp:cp-simulator"),
        label="EVCS Online Simulator",
    )
    operator = get_user_model().objects.create_user(
        username="charge-points-operator",
        email="charge-points-operator@example.com",
        password="secret",
    )
    Group.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)[0].user_set.add(operator)
    request = RequestFactory().get("/")
    request.user = operator

    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]
    charge_point_module = next(
        module for module in nav_modules if module.path == "/charge-points/"
    )
    visible_paths = {landing.path for landing in charge_point_module.enabled_landings}

    assert {
        reverse("ocpp:ocpp-dashboard"),
        reverse("ocpp:cp-simulator"),
    }.issubset(visible_paths)

def test_charge_points_module_hides_operator_only_map_link_from_anonymous_users():
    module = Module.objects.create(path="/charge-points/", menu="Charge Points")
    Landing.objects.create(
        module=module,
        path=reverse("ocpp:charging-station-map"),
        label="Charging Station Map",
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]
    charge_point_module = next(
        (module for module in nav_modules if module.path == "/charge-points/"),
        None,
    )

    assert charge_point_module is None


def test_docs_library_and_documents_require_login(client):
    library_url = reverse("docs:docs-library")
    document_url = reverse("docs:docs-document", args=["index.md"])
    apps_document_url = reverse("docs:apps-docs-document", args=["README.md"])

    library_response = client.get(library_url)
    document_response = client.get(document_url)
    apps_document_response = client.get(apps_document_url)

    expected_prefix = f"{reverse('pages:login')}?next="
    assert library_response.status_code == 302
    assert library_response.url.startswith(expected_prefix)
    assert document_response.status_code == 302
    assert document_response.url.startswith(expected_prefix)
    assert apps_document_response.status_code == 302
    assert apps_document_response.url.startswith(expected_prefix)


@pytest.mark.parametrize(
    ("group_name", "is_superuser"),
    [
        (PRODUCT_DEVELOPER_GROUP_NAME, False),
        (RELEASE_MANAGER_GROUP_NAME, False),
        (None, True),
    ],
)
def test_docs_library_and_documents_require_developer_or_release_manager_group(
    client, group_name, is_superuser
):
    user = get_user_model().objects.create_user(
        username=f"docs-group-user-{group_name or 'superuser'}",
        email=f"docs-group-user-{group_name or 'superuser'}@example.com",
        password="secret",
        is_staff=True,
        is_superuser=is_superuser,
    )
    library_url = reverse("docs:docs-library")
    document_url = reverse("docs:docs-document", args=["index.md"])
    client.force_login(user)
    library_response = client.get(library_url)
    document_response = client.get(document_url)

    expected_initial_status = 200 if is_superuser else 403
    assert library_response.status_code == expected_initial_status
    assert document_response.status_code == expected_initial_status

    if group_name:
        allowed_group, _ = SecurityGroup.objects.get_or_create(name=group_name)
        allowed_group.user_set.add(user)

    library_response = client.get(library_url)
    document_response = client.get(document_url)

    assert library_response.status_code == 200
    assert document_response.status_code == 200


def test_developers_module_pill_hidden_from_anonymous_users_when_landing_is_docs_library():
    module = Module.objects.create(path="/docs/", menu="Developers")
    Landing.objects.create(
        module=module,
        path=reverse("docs:docs-library"),
        label="Developer Documents",
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]

    assert not any(candidate.path == "/docs/" for candidate in nav_modules)


def test_developers_module_pill_hidden_from_non_member_users_when_landing_is_docs_library():
    module = Module.objects.create(path="/docs/", menu="Developers")
    Landing.objects.create(
        module=module,
        path=reverse("docs:docs-library"),
        label="Developer Documents",
    )
    user = get_user_model().objects.create_user(
        username="docs-non-member",
        email="docs-non-member@example.com",
        password="secret",
    )
    request = RequestFactory().get("/")
    request.user = user

    cache.clear()
    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]

    assert not any(candidate.path == "/docs/" for candidate in nav_modules)


def test_developers_module_pill_visible_to_release_manager_users():
    module = Module.objects.create(path="/docs/", menu="Developers")
    Landing.objects.create(
        module=module,
        path=reverse("docs:docs-library"),
        label="Developer Documents",
    )
    user = get_user_model().objects.create_user(
        username="docs-release-manager",
        email="docs-release-manager@example.com",
        password="secret",
    )
    release_manager_group, _ = SecurityGroup.objects.get_or_create(
        name=RELEASE_MANAGER_GROUP_NAME
    )
    release_manager_group.user_set.add(user)
    request = RequestFactory().get("/")
    request.user = user

    cache.clear()
    nav_context = context_processors.nav_links(request)
    nav_modules = nav_context["nav_modules"]

    assert any(candidate.path == "/docs/" for candidate in nav_modules)


def test_docs_library_renders_indexed_documents_before_other_documents(client):
    staff_user = get_user_model().objects.create_user(
        username="docs-index-staff",
        email="docs-index-staff@example.com",
        password="secret",
        is_staff=True,
    )
    course_group = SecurityGroup.objects.create(name="Safety Course")
    course_group.user_set.add(staff_user)
    indexed = DocumentIndex.objects.create(
        title="Security Model Guide",
        doc_path="docs/security-model.md",
        listable=True,
    )
    DocumentIndexAssignment.objects.create(
        document=indexed,
        security_group=course_group,
        access=DocumentIndex.ACCESS_REQUIRED,
    )

    client.force_login(staff_user)
    response = client.get(reverse("docs:docs-library"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Indexed Documents" in body
    assert "Other Documents" in body
    assert body.index("Indexed Documents") < body.index("Other Documents")
    assert "security-model.md" in body
    assert "Manage indexed documents" in body
    assert "Create indexed document" in body


def test_readme_resolves_sigils_for_authenticated_user(client):
    user = get_user_model().objects.create_user(
        username="docs-sigil-user",
        email="docs-sigil-user@example.com",
        password="secret",
    )
    document = Path("docs/sigil-test.md")
    document.write_text("# Sigils\n\nCurrent path: [REQ.path]\n", encoding="utf-8")
    client.force_login(user)
    try:
        response = client.get(reverse("docs:docs-document", args=["sigil-test.md"]))
        assert response.status_code == 200
        assert "Current path: /docs/sigil-test.md" in response.content.decode()
    finally:
        document.unlink(missing_ok=True)


def test_docs_library_preserves_docs_prefix_for_indexed_document_matching(client):
    staff_user = get_user_model().objects.create_user(
        username="docs-prefix-match-staff",
        email="docs-prefix-match-staff@example.com",
        password="secret",
        is_staff=True,
    )
    course_group = SecurityGroup.objects.create(name="Platform Course")
    course_group.user_set.add(staff_user)
    indexed = DocumentIndex.objects.create(
        title="Security Model Guide",
        doc_path="docs/security-model.md",
        listable=True,
    )
    DocumentIndexAssignment.objects.create(
        document=indexed,
        security_group=course_group,
        access=DocumentIndex.ACCESS_REQUIRED,
    )

    client.force_login(staff_user)
    response = client.get(reverse("docs:docs-library"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Platform Course" in body
    assert "security-model.md" in body


def test_docs_library_hides_restricted_assignment_from_non_member_users(client):
    user = get_user_model().objects.create_user(
        username="docs-restricted-user",
        email="docs-restricted-user@example.com",
        password="secret",
        is_staff=True,
    )
    restricted_group = SecurityGroup.objects.create(name="Restricted Course")
    document = Path("docs/restricted-visibility-test.md")
    document.write_text("# Restricted\n\nHidden document.\n", encoding="utf-8")
    indexed = DocumentIndex.objects.create(
        title="Restricted Visibility Test",
        doc_path="docs/restricted-visibility-test.md",
        listable=True,
    )
    DocumentIndexAssignment.objects.create(
        document=indexed,
        security_group=restricted_group,
        access=DocumentIndex.ACCESS_RESTRICTED,
    )

    client.force_login(user)
    try:
        response = client.get(reverse("docs:docs-library"))
        body = response.content.decode()
        assert response.status_code == 200
        assert "Restricted Visibility Test" not in body
        assert "restricted-visibility-test.md" not in body
    finally:
        document.unlink(missing_ok=True)


def test_docs_library_keeps_nested_docs_visible_and_shows_parent_navigation(client):
    user = get_user_model().objects.create_user(
        username="docs-nested-user",
        email="docs-nested-user@example.com",
        password="secret",
        is_staff=True,
    )
    nested_document = Path("docs/library-test/subfolder/nested-visibility-test.md")
    nested_document.parent.mkdir(parents=True, exist_ok=True)
    nested_document.write_text("# Nested visibility\n\nNested document.\n", encoding="utf-8")

    client.force_login(user)
    try:
        cache.clear()
        root_response = client.get(reverse("docs:docs-library"))
        folder_response = client.get(reverse("docs:docs-library"), {"docs_path": "library-test/subfolder"})

        assert root_response.status_code == 200
        assert "library-test/" in root_response.content.decode()
        assert "nested-visibility-test.md" not in root_response.content.decode()
        assert folder_response.status_code == 200
        assert "Up one level" in folder_response.content.decode()
    finally:
        nested_document.unlink(missing_ok=True)
        for parent in (nested_document.parent, nested_document.parent.parent):
            if parent.exists():
                parent.rmdir()


def test_docs_library_groups_root_documents_into_root_folder(client):
    user = get_user_model().objects.create_user(
        username="docs-root-folder-user",
        email="docs-root-folder-user@example.com",
        password="secret",
        is_staff=True,
    )
    root_document = Path("docs/library-root-visibility-test.md")
    root_document.write_text("# Root visibility\n\nRoot document.\n", encoding="utf-8")

    client.force_login(user)
    try:
        cache.clear()
        root_response = client.get(reverse("docs:docs-library"))
        virtual_root_response = client.get(
            reverse("docs:docs-library"),
            {"docs_path_virtual_root": "1"},
        )

        assert root_response.status_code == 200
        assert "root/" in root_response.content.decode()
        assert "library-root-visibility-test.md" not in root_response.content.decode()
        assert virtual_root_response.status_code == 200
        assert "library-root-visibility-test.md" in virtual_root_response.content.decode()
    finally:
        root_document.unlink(missing_ok=True)


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

    client.force_login(user)
    try:
        cache.clear()
        folder_response = client.get(reverse("docs:docs-library"), {"docs_path": "__root__"})

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

    client.force_login(user)
    try:
        cache.clear()
        response = client.get(reverse("docs:docs-library"), {"docs_path": "library-prefix-match.md"})

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
