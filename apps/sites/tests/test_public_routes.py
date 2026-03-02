import datetime
import json
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes

from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.ocpp.consumers.constants import (
    OCPP_VERSION_16,
    OCPP_VERSION_201,
    OCPP_VERSION_21,
)
from apps.modules.models import Module
from apps.sites.models import Landing
from apps.sites.views.landing import SUPPORTED_OCPP_VERSIONS


@pytest.fixture
def user(db):
    """Create a regular user for authenticated route checks."""

    return get_user_model().objects.create_user(
        username="route-user", email="route-user@example.com", password="secret"
    )


@pytest.fixture
def staff_user(db):
    """Create a staff user for staff-only route checks."""

    return get_user_model().objects.create_user(
        username="route-staff",
        email="route-staff@example.com",
        password="secret",
        is_staff=True,
    )


@pytest.mark.django_db
def test_public_pages_render_for_anonymous(client):
    """Anonymous users should be able to render public pages."""

    response = client.get(reverse("pages:index"))
    assert response.status_code == 200
    assert reverse("pages:user-story-submit") in response.content.decode()

    changelog_response = client.get(reverse("pages:changelog"))
    assert changelog_response.status_code == 200
    assert any(t.name == "pages/changelog.html" for t in changelog_response.templates)

    client_report_response = client.get(reverse("pages:client-report"))
    assert client_report_response.status_code == 200
    assert any(
        t.name == "pages/client_report.html" for t in client_report_response.templates
    )


@pytest.mark.django_db
def test_public_home_hides_feedback_button_when_feedback_ingestion_disabled(client):
    """Regression: public home should hide feedback UI when ingestion feature is disabled."""

    Feature.objects.update_or_create(
        slug="feedback-ingestion",
        defaults={"display": "Feedback Ingestion", "is_enabled": False},
    )

    response = client.get(reverse("pages:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="user-story-toggle"' not in content
    assert 'id="footer-placeholder"' in content
    assert 'pages/js/base.js' in content


@pytest.mark.django_db
def test_operator_interface_notice_page_renders_supported_versions(client):
    """Operator notice page should render websocket guidance and OCPP versions."""

    response = client.get(reverse("pages:operator-interface-notice"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "wss://testserver/&lt;charge_point_id&gt;/" in content
    for version in SUPPORTED_OCPP_VERSIONS:
        assert f"OCPP {version}" in content




@pytest.mark.django_db
def test_operator_interface_notice_versions_match_ocpp_negotiation_constants():
    """Regression: operator notice versions should match negotiated OCPP protocols."""

    assert SUPPORTED_OCPP_VERSIONS == (
        OCPP_VERSION_16.removeprefix("ocpp"),
        OCPP_VERSION_201.removeprefix("ocpp"),
        OCPP_VERSION_21.removeprefix("ocpp"),
    )

@pytest.mark.django_db
def test_operator_interface_notice_page_is_get_only(client):
    """Operator notice page should reject non-GET requests."""

    response = client.post(reverse("pages:operator-interface-notice"))

    assert response.status_code == 405

def test_footer_fragment_is_get_only(client):
    """The footer fragment endpoint should reject non-GET methods."""

    response = client.get(reverse("pages:footer-fragment"))
    assert response.status_code == 200
    assert any(t.name == "core/footer.html" for t in response.templates)

    rejected = client.post(reverse("pages:footer-fragment"))
    assert rejected.status_code == 405


@pytest.mark.django_db
def test_user_story_submit_is_post_only(client):
    """The user-story endpoint should reject GET requests."""

    rejected = client.get(reverse("pages:user-story-submit"))
    assert rejected.status_code == 405


@pytest.mark.django_db
def test_release_checklist_requires_staff(client, user, staff_user):
    """Release checklist access should be limited to staff users."""

    url = reverse("pages:release-checklist")

    anonymous_response = client.get(url)
    assert anonymous_response.status_code == 302

    client.force_login(user)
    non_staff_response = client.get(url)
    assert non_staff_response.status_code in {302, 403}

    client.force_login(staff_user)
    staff_response = client.get(url)
    assert staff_response.status_code in {200, 404}
    if staff_response.status_code == 200:
        assert any(t.name == "docs/readme.html" for t in staff_response.templates)


@pytest.mark.django_db
def test_client_report_download_enforces_login_and_ownership(
    client, user, staff_user, monkeypatch, tmp_path
):
    """Client report download should require login and owner/staff permissions."""

    User = get_user_model()
    owner = User.objects.create_user(
        username="report-owner", email="owner@example.com", password="secret"
    )
    other_user = User.objects.create_user(
        username="report-other", email="other@example.com", password="secret"
    )
    report = ClientReport.objects.create(
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 1, 31),
        owner=owner,
        data={},
    )
    download_url = reverse("pages:client-report-download", args=[report.pk])

    login_required_response = client.get(download_url)
    assert login_required_response.status_code == 302

    client.force_login(other_user)
    forbidden_response = client.get(download_url)
    assert forbidden_response.status_code == 403

    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n%EOF")
    monkeypatch.setattr(ClientReport, "ensure_pdf", lambda self: Path(pdf_file))

    client.force_login(owner)
    owner_response = client.get(download_url)
    assert owner_response.status_code == 200
    assert owner_response["Content-Type"] == "application/pdf"

    client.force_login(staff_user)
    staff_response = client.get(download_url)
    assert staff_response.status_code == 200
    assert staff_response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
def test_invitation_login_invalid_tokens_are_handled_safely(client):
    """Invitation login should safely reject malformed/invalid tokens."""

    user = get_user_model().objects.create_user(
        username="invite-user", email="invite-user@example.com", password="secret"
    )
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    response = client.get(reverse("pages:invitation-login", args=[uid, "bad-token"]))

    assert response.status_code == 400
    assert "Invalid invitation link" in response.content.decode()

    malformed_uid_response = client.get(
        reverse("pages:invitation-login", args=["!!invalid!!", "bad-token"])
    )
    assert malformed_uid_response.status_code == 400


@pytest.mark.django_db
def test_whatsapp_webhook_get_not_allowed(client, settings):
    """Webhook should reject GET requests."""

    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    response = client.get(url)
    assert response.status_code == 405


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload, expected_status",
    [
        (json.dumps({"from": "+15551234", "message": "Hello"}), 201),
        ("{not-json}", 400),
        (json.dumps({"from": "", "message": ""}), 400),
    ],
)
def test_whatsapp_webhook_post_payload_validation(
    client, settings, payload, expected_status
):
    """Webhook should validate POST payload format and content."""

    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    response = client.post(url, data=payload, content_type="application/json")
    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json()["status"] == "ok"


@pytest.mark.django_db
@pytest.mark.regression
def test_operator_site_interface_disabled_returns_blank_public_home(client):
    """Home should render a blank page when the interface feature is disabled."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={"display": "Operator Site Interface", "is_enabled": False},
    )

    response = client.get(reverse("pages:index"))

    assert response.status_code == 200
    body_match = re.search(rb"<body[^>]*>(.*?)</body>", response.content, re.DOTALL)
    assert body_match is not None
    assert body_match.group(1).strip() == b""


@pytest.mark.django_db
@pytest.mark.regression
def test_operator_site_interface_redirects_to_configured_interface_landing(client):
    """Disabled interface feature should redirect home to configured interface landing."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={"display": "Operator Site Interface", "is_enabled": False},
    )
    module = Module.objects.create(path="operator")
    landing = Landing.objects.create(module=module, path="/operator/", label="Operator")

    site, _created = Site.objects.get_or_create(
        domain="testserver",
        defaults={"name": "testserver"},
    )
    site.interface_landing = landing
    site.save(update_fields=["interface_landing"])

    response = client.get(reverse("pages:index"))

    assert response.status_code == 302
    assert response["Location"] == "/operator/?operator_interface=1"


@pytest.mark.django_db
@pytest.mark.regression
def test_operator_site_interface_landing_with_query_avoids_redirect_loop(client):
    """Landing redirects once and then renders without self-redirect loops."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={"display": "Operator Site Interface", "is_enabled": False},
    )
    module = Module.objects.create(path="operator-loop")
    landing = Landing.objects.create(
        module=module,
        path="/?foo=1",
        label="Operator Loop Safe",
    )

    site, _created = Site.objects.get_or_create(
        domain="testserver",
        defaults={"name": "testserver"},
    )
    site.interface_landing = landing
    site.save(update_fields=["interface_landing"])

    first = client.get(reverse("pages:index"))
    assert first.status_code == 302
    assert first["Location"] == "/?foo=1&operator_interface=1"

    second = client.get(first["Location"])
    assert second.status_code == 200


@pytest.mark.django_db
@pytest.mark.regression
def test_operator_site_interface_blocks_unsafe_redirect_targets(client):
    """Unsafe absolute/scheme-relative targets should not redirect users away."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={"display": "Operator Site Interface", "is_enabled": False},
    )
    module = Module.objects.create(path="operator-unsafe")
    landing = Landing.objects.create(
        module=module,
        path="//malicious.example/phish",
        label="Unsafe",
    )

    site, _created = Site.objects.get_or_create(
        domain="testserver",
        defaults={"name": "testserver"},
    )
    site.interface_landing = landing
    site.save(update_fields=["interface_landing"])

    response = client.get(reverse("pages:index"))

    assert response.status_code == 200
    assert b"<body" in response.content


@pytest.mark.django_db
@pytest.mark.regression
def test_operator_interface_mode_query_param_alone_does_not_hide_navigation(client):
    """Anonymous query-string toggles must not suppress public navigation chrome."""

    response = client.get(f"{reverse('pages:index')}?operator_interface=1")

    assert response.status_code == 200
    assert b"<nav" in response.content
