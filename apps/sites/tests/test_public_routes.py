import datetime
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.modules.models import Module
from apps.sites.models import Landing

pytestmark = [pytest.mark.django_db]

DARK_THEME_BACKGROUND_STYLE = "background: #111827;"


@pytest.fixture
def user(db):
    """Create a regular user for authenticated route checks.

    Parameters:
        db: Enables database access for fixture setup.

    Returns:
        User: A persisted non-staff user instance.

    Raises:
        None.
    """

    return get_user_model().objects.create_user(
        username="route-user", email="route-user@example.com", password="secret"
    )


@pytest.fixture
def staff_user(db):
    """Create a staff user for staff-only route checks.

    Parameters:
        db: Enables database access for fixture setup.

    Returns:
        User: A persisted staff user instance.

    Raises:
        None.
    """

    return get_user_model().objects.create_user(
        username="route-staff",
        email="route-staff@example.com",
        password="secret",
        is_staff=True,
    )


def test_client_report_download_enforces_login_and_ownership(
    client, user, staff_user, monkeypatch, tmp_path
):
    """Require login and owner or staff access for report downloads.

    Parameters:
        client: Django test client for route requests.
        user: Baseline authenticated user fixture.
        staff_user: Staff user fixture used for elevated access assertions.
        monkeypatch: Fixture used to stub report PDF generation.
        tmp_path: Temporary filesystem path for a fake PDF.

    Returns:
        None: Assertions validate response codes and content types.

    Raises:
        AssertionError: If route authorization or response payloads regress.
    """

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="report-owner", email="owner@example.com", password="secret"
    )
    other_user = user_model.objects.create_user(
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


def test_invitation_login_invalid_tokens_are_handled_safely(client):
    """Reject malformed invitation UID and token payloads.

    Parameters:
        client: Django test client for unauthenticated invitation requests.

    Returns:
        None: Assertions validate HTTP 400 responses for invalid inputs.

    Raises:
        AssertionError: If invalid links stop returning safe client errors.
    """

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="invite-user", email="invite-user@example.com", password="secret"
    )
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    invalid_token_response = client.get(
        reverse("pages:invitation-login", args=[uid, "bad-token"])
    )
    assert invalid_token_response.status_code == 400
    assert "Invalid invitation link" in invalid_token_response.content.decode()

    malformed_uid_response = client.get(
        reverse("pages:invitation-login", args=["!!invalid!!", "bad-token"])
    )
    assert malformed_uid_response.status_code == 400


def test_whatsapp_webhook_post_payload_validation(client, settings):
    """Validate webhook JSON payload content and malformed request handling.

    Parameters:
        client: Django test client for webhook requests.
        settings: Django settings fixture for feature flag toggles.

    Returns:
        None: Assertions validate accepted and rejected webhook payloads.

    Raises:
        AssertionError: If webhook validation behavior regresses.
    """

    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    success = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert success.status_code == 201
    assert success.json()["status"] == "ok"

    invalid_json = client.post(url, data="{not-json}", content_type="application/json")
    assert invalid_json.status_code == 400

    empty_fields = client.post(
        url,
        data=json.dumps({"from": "", "message": ""}),
        content_type="application/json",
    )
    assert empty_fields.status_code == 400


def test_whatsapp_webhook_requires_post_and_feature_flag(client, settings):
    """Enforce webhook method guardrails and feature-flag availability checks.

    Parameters:
        client: Django test client for webhook requests.
        settings: Django settings fixture for feature flag toggles.

    Returns:
        None: Assertions validate method and feature-flag guard branches.

    Raises:
        AssertionError: If method restriction or disabled-mode behavior regresses.
    """

    url = reverse("pages:whatsapp-webhook")

    method_not_allowed = client.get(url)
    assert method_not_allowed.status_code == 405

    settings.PAGES_WHATSAPP_ENABLED = False
    disabled = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert disabled.status_code == 503


def test_operator_site_interface_blocks_unsafe_redirect_targets(client):
    """Ensure unsafe interface redirect targets are not followed.

    Parameters:
        client: Django test client for homepage rendering.

    Returns:
        None: Assertions validate rendered fallback content for unsafe paths.

    Raises:
        AssertionError: If unsafe interface targets start redirecting users.
    """

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
    content = response.content.decode()
    assert 'id="operator-interface-title"' in content
    assert "ws://testserver/&lt;charge_point_id&gt;/" in content
    assert DARK_THEME_BACKGROUND_STYLE in content


def test_release_checklist_requires_staff(client, staff_user, user):
    """Verify release checklist access is restricted to staff users.

    Parameters:
        client: Django test client for authenticated and anonymous requests.
        staff_user: Staff user fixture expected to access the view.
        user: Non-staff user fixture expected to be denied.

    Returns:
        None: Assertions validate status codes for each permission level.

    Raises:
        AssertionError: If staff-only access control regresses.
    """

    url = reverse("pages:release-checklist")

    anon_response = client.get(url)
    assert anon_response.status_code == 302
    assert reverse("admin:login") in anon_response["Location"]

    client.force_login(user)
    non_staff_response = client.get(url)
    assert non_staff_response.status_code == 403

    client.force_login(staff_user)
    staff_response = client.get(url)
    assert staff_response.status_code in (200, 404)


def test_release_checklist_denies_inactive_staff(client):
    """Ensure inactive staff sessions cannot access staff-only checklist views.

    Parameters:
        client: Django test client for authenticated route requests.

    Returns:
        None: Assertion verifies inactive staff receive HTTP 403.

    Raises:
        AssertionError: If inactive staff accounts are incorrectly authorized.
    """

    user = get_user_model().objects.create_user(
        username="inactive-staff",
        email="inactive-staff@example.com",
        password="secret",
        is_staff=True,
        is_active=False,
    )
    client.force_login(user)

    response = client.get(reverse("pages:release-checklist"))

    assert response.status_code == 403
