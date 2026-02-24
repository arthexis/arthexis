import datetime
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes

from apps.energy.models import ClientReport


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
        t.name == "pages/client_report.html"
        for t in client_report_response.templates
    )


@pytest.mark.django_db
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
@pytest.mark.parametrize(
    "method, expected_status",
    [
        ("get", 405),
        ("post", 201),
    ],
)
def test_whatsapp_webhook_method_and_payload_validation(
    client, settings, method, expected_status
):
    """Webhook should only allow POST and validate payload format/content."""

    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    if method == "get":
        response = client.get(url)
        assert response.status_code == expected_status
        return

    valid_response = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert valid_response.status_code == expected_status
    assert valid_response.json()["status"] == "ok"

    malformed_response = client.post(
        url,
        data="{not-json}",
        content_type="application/json",
    )
    assert malformed_response.status_code == 400

    missing_fields_response = client.post(
        url,
        data=json.dumps({"from": "", "message": ""}),
        content_type="application/json",
    )
    assert missing_fields_response.status_code == 400
