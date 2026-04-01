import datetime
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core import changelog
from apps.energy.models import ClientReport
from apps.features.models import Feature
from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.modules.models import Module
from apps.sites.models import Landing
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
    assert client.get(download_url).status_code == 403

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


def test_whatsapp_webhook_requires_post_and_feature_flag(client, settings):
    url = reverse("pages:whatsapp-webhook")

    assert client.get(url).status_code == 405

    settings.PAGES_WHATSAPP_ENABLED = False
    disabled = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert disabled.status_code == 404


def test_whatsapp_webhook_post_payload_validation(client, settings):
    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    success = client.post(
        url,
        data=json.dumps({"from": "+15551234", "message": "Hello"}),
        content_type="application/json",
    )
    assert success.status_code == 201
    assert success.json()["status"] == "ok"

    assert client.post(url, data="{not-json}", content_type="application/json").status_code == 400

    empty_fields = client.post(
        url,
        data=json.dumps({"from": "", "message": ""}),
        content_type="application/json",
    )
    assert empty_fields.status_code == 400


def test_operator_site_interface_blocks_unsafe_redirect_targets(client):
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
    assert 'id="operator-interface-title"' in response.content.decode()


def test_release_checklist_requires_staff(client):
    url = reverse("pages:release-checklist")

    anon_response = client.get(url)
    assert anon_response.status_code == 302
    assert reverse("admin:login") in anon_response["Location"]

    user = get_user_model().objects.create_user(
        username="route-user", email="route-user@example.com", password="secret"
    )
    client.force_login(user)
    assert client.get(url).status_code == 403

    staff_user = get_user_model().objects.create_user(
        username="route-staff-checklist",
        email="route-staff-checklist@example.com",
        password="secret",
        is_staff=True,
    )
    client.force_login(staff_user)
    assert client.get(url).status_code in (200, 404)


def test_require_site_operator_or_staff_enforces_admin_operator_boundary(rf):
    """Regression: site operators are allowed while non-operator users are denied."""

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
    Group.objects.get_or_create(name=SITE_OPERATOR_GROUP_NAME)[0].user_set.add(operator_user)
    request.user = operator_user
    assert require_site_operator_or_staff(request) is None


def test_changelog_data_validates_negative_query_params(client):
    url = reverse("pages:changelog-data")

    assert client.get(url, {"page": "0"}).status_code == 400
    assert client.get(url, {"offset": "-1"}).status_code == 400


def test_changelog_data_hides_internal_exception_messages(client, monkeypatch):
    url = reverse("pages:changelog-data")

    def raise_error(*args, **kwargs):
        raise changelog.ChangelogError("sensitive details")

    monkeypatch.setattr(changelog, "get_page", raise_error)

    response = client.get(url, {"page": "1", "offset": "0"})

    assert response.status_code == 500
    assert response.json() == {"error": "Unable to load additional updates."}
