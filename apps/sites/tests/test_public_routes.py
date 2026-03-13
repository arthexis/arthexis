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


def test_client_report_download_enforces_login_and_ownership(
    client, user, staff_user, monkeypatch, tmp_path
):
    """Client report download should require login and owner/staff permissions."""

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
    """Invitation login should reject malformed and invalid token payloads."""

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
    """Webhook should validate JSON payload content and reject malformed input."""

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


def test_operator_site_interface_blocks_unsafe_redirect_targets(client):
    """Unsafe absolute and scheme-relative interface targets should not redirect."""

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
