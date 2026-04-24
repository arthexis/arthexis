"""Regression coverage for the public charger vendor submission flow."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse

from apps.tasks.models import ChargerVendorSubmission

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def clear_rate_limit_cache():
    """Ensure each regression test starts with an empty rate-limit cache."""

    cache.clear()
    yield
    cache.clear()

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
    assert submission.is_user_data is True

    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert (
        "Thanks for sharing your charger portfolio. Our team will review the submission and follow up about the integration fit."
        in messages
    )

def test_charger_vendor_submission_rate_limits_repeated_posts(client):
    """Regression: repeated public submissions should eventually be throttled."""

    payload = {
        "company_name": "Vendor Grid",
        "contact_name": "Avery Watts",
        "contact_email": "avery@vendorgrid.example",
        "charger_brand": "VoltArc",
        "charger_models": "VA-60 DC",
        "ocpp_versions": "OCPP 1.6J",
        "connectivity_summary": "LTE and ethernet.",
        "remote_access_method": "Support portal plus remote logs.",
        "integration_goals": "Use Arthexis as the OCPP pivot.",
    }

    for _ in range(5):
        response = client.post(reverse("tasks:charger-vendor-submission"), data=payload)
        assert response.status_code == 302

    throttled_response = client.post(
        reverse("tasks:charger-vendor-submission"),
        data=payload,
    )

    assert throttled_response.status_code == 429
