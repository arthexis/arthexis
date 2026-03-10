import datetime
import json
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.test.html import Element, parse_html
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

def _elements_with_class(element: Element, class_name: str) -> list[Element]:
    """Return all elements that include the given CSS class."""

    matches: list[Element] = []
    classes = dict(element.attributes).get("class", "")
    if class_name in classes.split():
        matches.append(element)
    for child in element.children:
        if isinstance(child, Element):
            matches.extend(_elements_with_class(child, class_name))
    return matches

def _find_all_elements(element: Element, tag_name: str, **attributes: str) -> list[Element]:
    """Return all descendants whose tag and attributes match."""

    matches: list[Element] = []
    if element.name == tag_name and _has_attributes(element, attributes):
        matches.append(element)
    for child in element.children:
        if isinstance(child, Element):
            matches.extend(_find_all_elements(child, tag_name, **attributes))
    return matches

def _find_element(element: Element, tag_name: str, **attributes: str) -> Element | None:
    """Return the first descendant whose tag and attributes match."""

    matches = _find_all_elements(element, tag_name, **attributes)
    return matches[0] if matches else None

def _has_attributes(element: Element, attributes: dict[str, str]) -> bool:
    """Check whether an element includes all expected attributes."""

    element_attributes = dict(element.attributes)
    return all(element_attributes.get(name) == value for name, value in attributes.items())

def _normalized_row_text(element: Element) -> str:
    """Flatten an element to normalized text for robust label assertions."""

    return " ".join(str(element).split())

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

def test_whatsapp_webhook_get_not_allowed(client, settings):
    """Webhook should reject GET requests."""

    settings.PAGES_WHATSAPP_ENABLED = True
    url = reverse("pages:whatsapp-webhook")

    response = client.get(url)
    assert response.status_code == 405

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
    content = response.content.decode()
    assert 'id="operator-interface-title"' in content
    assert "ws://testserver/&lt;charge_point_id&gt;/" in content
    assert DARK_THEME_BACKGROUND_STYLE in content

