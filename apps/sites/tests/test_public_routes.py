import datetime
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.sites.models import Site
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
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


def test_docs_module_pill_hidden_from_anonymous_users_when_landing_is_docs_library():
    module = Module.objects.create(path="/docs/", menu="Docs")
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
