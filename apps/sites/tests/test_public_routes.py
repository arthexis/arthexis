import datetime
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.energy.models import ClientReport
from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
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
