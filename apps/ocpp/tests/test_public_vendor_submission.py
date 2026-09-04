"""Regression coverage for the public charger vendor submission flow."""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from django.views import View

from apps.ocpp.models import ChargerVendorSubmission

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_rate_limit_cache():
    """Ensure each regression test starts with an empty rate-limit cache."""

    cache.clear()
    yield
    cache.clear()


@override_settings(
    CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED=True,
    ROOT_URLCONF="apps.ocpp.tests.intake_test_urls",
)
def test_charger_vendor_submission_persists_submission_and_redirects(client):
    """Regression: valid public vendor submissions should be stored for admin review."""

    response = client.post(
        reverse("ocpp_intake:charger-vendor-submission"),
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
            "remote_access_method": (
                "Support portal plus remote logs and firmware bundles."
            ),
            "hardware_notes": "CCS1, NACS, MID meter, contactless terminal.",
            "integration_goals": (
                "Use Arthexis as the OCPP pivot for monitoring, firmware, and "
                "workflow orchestration."
            ),
            "additional_notes": "Sandbox credentials available on request.",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain[-1][0].endswith(
        reverse("ocpp_intake:charger-vendor-submission-thanks")
    )
    submission = ChargerVendorSubmission.objects.get()
    assert submission.company_name == "Vendor Grid"
    assert submission.charger_brand == "VoltArc"
    assert submission.review_status == ChargerVendorSubmission.ReviewStatus.PENDING
    assert submission.ocpp_versions == "OCPP 1.6J OCPP 2.0.1"
    assert submission.is_user_data is True

    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert (
        "Thanks for sharing your charger portfolio. Our team will review the "
        "submission and follow up about the integration fit."
    ) in messages


@override_settings(
    CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED=True,
    ROOT_URLCONF="apps.ocpp.tests.intake_test_urls",
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
        response = client.post(
            reverse("ocpp_intake:charger-vendor-submission"),
            data=payload,
        )
        assert response.status_code == 302

    throttled_response = client.post(
        reverse("ocpp_intake:charger-vendor-submission"),
        data=payload,
    )

    assert throttled_response.status_code == 429


def test_ocpp_routes_exclude_public_intake_without_feature_pack(settings):
    """OCPP routing alone must not expose the unauthenticated intake form."""

    from apps.ocpp import routes

    original_value = settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED
    settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED = False
    reloaded = importlib.reload(routes)
    try:
        assert [str(pattern.pattern) for pattern in reloaded.ROOT_URLPATTERNS] == [
            "ocpp/"
        ]
    finally:
        settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED = original_value
        importlib.reload(routes)


def test_ocpp_routes_include_public_intake_with_feature_pack(settings):
    """The retired app's feature pack remains the explicit intake opt-in."""

    from apps.ocpp import routes

    original_value = settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED
    settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED = True
    reloaded = importlib.reload(routes)
    try:
        assert [str(pattern.pattern) for pattern in reloaded.ROOT_URLPATTERNS] == [
            "",
            "ocpp/",
        ]
    finally:
        settings.CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED = original_value
        importlib.reload(routes)


def test_no_rates_fallback_rate_limits_repeated_posts(monkeypatch, rf):
    """No-Rates profiles should keep the public intake fallback throttle."""

    from apps.ocpp.views import intake as intake_views

    original_is_installed = django_apps.is_installed

    def fake_is_installed(app_label):
        if app_label == "apps.rates":
            return False
        return original_is_installed(app_label)

    monkeypatch.setattr(django_apps, "is_installed", fake_is_installed)
    reloaded = importlib.reload(intake_views)
    try:

        class ProbeView(reloaded.RateLimitedViewMixin, View):
            rate_limit_scope = "charger-vendor-submission-test"
            rate_limit_fallback = 5
            rate_limit_window = 3600

            def post(self, request):
                return HttpResponse("ok")

        view = ProbeView.as_view()
        for _ in range(5):
            response = view(rf.post("/", REMOTE_ADDR="203.0.113.42"))
            assert response.status_code == 200

        throttled_response = view(rf.post("/", REMOTE_ADDR="203.0.113.42"))
    finally:
        monkeypatch.setattr(django_apps, "is_installed", original_is_installed)
        importlib.reload(intake_views)

    assert throttled_response.status_code == 429
