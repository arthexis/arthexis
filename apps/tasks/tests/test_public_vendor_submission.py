"""Regression coverage for the public charger vendor submission flow."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.tasks.models import ChargerVendorSubmission


@pytest.mark.django_db
def test_charger_vendor_submission_page_renders_public_form(client):
    """Regression: vendors should be able to access the intake page without authentication."""

    response = client.get(reverse("tasks:charger-vendor-submission"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Share your chargers for Arthexis integration review" in content
    assert "Supported OCPP versions" in content
    assert "Remote access and support workflow" in content


@pytest.mark.django_db
def test_charger_vendor_submission_persists_submission_and_redirects(client):
    """Regression: valid public vendor submissions should be stored for admin review."""

    response = client.post(
        reverse("tasks:charger-vendor-submission"),
        data={
            "company_name": "Vendor Grid",
            "contact_name": "Avery Watts",
            "contact_email": "avery@vendorgrid.example",
            "contact_phone": "+1 555 0110",
            "website": "https://vendorgrid.example",
            "charger_brand": "VoltArc",
            "charger_models": "VA-60 DC\nVA-22 AC",
            "ocpp_versions": " OCPP 1.6J   OCPP 2.0.1 ",
            "connectivity_summary": "LTE and ethernet with optional VPN.",
            "api_documentation_url": "https://vendorgrid.example/docs",
            "certification_summary": "UL listed and regional EMC approvals.",
            "deployment_regions": "United States, Mexico",
            "deployment_volume": "1,200 chargers",
            "remote_access_method": "Support portal plus remote logs and firmware bundles.",
            "hardware_notes": "CCS1, NACS, MID meter, contactless terminal.",
            "integration_goals": "Use Arthexis as the OCPP pivot for monitoring, firmware, and workflow orchestration.",
            "additional_notes": "Sandbox credentials available on request.",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain[-1][0].endswith(reverse("tasks:charger-vendor-submission-thanks"))
    submission = ChargerVendorSubmission.objects.get()
    assert submission.company_name == "Vendor Grid"
    assert submission.charger_brand == "VoltArc"
    assert submission.review_status == ChargerVendorSubmission.ReviewStatus.PENDING
    assert submission.ocpp_versions == "OCPP 1.6J OCPP 2.0.1"

    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert any("review the submission" in message for message in messages)


@pytest.mark.django_db
def test_charger_vendor_submission_requires_core_fields(client):
    """Regression: invalid public submissions should stay on the form with errors."""

    response = client.post(reverse("tasks:charger-vendor-submission"), data={"company_name": ""})

    assert response.status_code == 200
    assert ChargerVendorSubmission.objects.count() == 0
    assert "This field is required." in response.content.decode()
