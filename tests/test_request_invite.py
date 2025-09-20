import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import InviteLead
from pages import views as pages_views


_TIMESTAMP_WIDGET = pages_views.InvitationRequestForm().fields["timestamp"].widget


def _timestamp_value(extra_seconds: int = 2) -> str:
    """Return a formatted timestamp older than the minimum submission interval."""

    offset = pages_views.INVITATION_REQUEST_MIN_SUBMISSION_INTERVAL + datetime.timedelta(
        seconds=extra_seconds
    )
    return _TIMESTAMP_WIDGET.format_value(timezone.now() - offset)


@pytest.fixture(autouse=True)
def stub_invitation_dependencies(monkeypatch):
    """Avoid external dependencies when exercising the invite request view."""

    monkeypatch.setattr(
        pages_views.public_wifi, "resolve_mac_address", lambda *_: None, raising=False
    )
    monkeypatch.setattr(
        pages_views.Node, "get_local", classmethod(lambda cls: None), raising=False
    )
    monkeypatch.setattr(
        pages_views.mailer,
        "send",
        lambda *args, **kwargs: "ok",
        raising=False,
    )


def test_request_invite_accepts_valid_submission():
    client = Client()
    email = "valid-invite@example.com"
    InviteLead.objects.filter(email=email).delete()

    response = client.post(
        reverse("pages:request-invite"),
        {
            "email": email,
            "comment": "I'd like to try the platform.",
            "timestamp": _timestamp_value(),
            "honeypot": "",
        },
    )

    assert response.status_code == 200
    assert InviteLead.objects.filter(email=email).count() == 1
    assert response.context["sent"] is True
    assert not response.context["form"].errors


def test_request_invite_rejects_honeypot_submission():
    client = Client()
    email = "bot@example.com"
    InviteLead.objects.filter(email=email).delete()

    response = client.post(
        reverse("pages:request-invite"),
        {
            "email": email,
            "comment": "",
            "timestamp": _timestamp_value(),
            "honeypot": "spam bot",
        },
    )

    assert response.status_code == 200
    assert not InviteLead.objects.filter(email=email).exists()
    errors = response.context["form"].non_field_errors()
    assert pages_views.INVITATION_REQUEST_HONEYPOT_MESSAGE in errors


def test_request_invite_throttles_repeated_requests():
    client = Client()
    email = "limit@example.com"
    ip_address = "203.0.113.10"
    InviteLead.objects.filter(email=email).delete()

    limit = pages_views.INVITATION_REQUEST_THROTTLE_LIMIT
    for _ in range(limit):
        lead = InviteLead.objects.create(
            email=email,
            comment="",
            path="/request-invite/",
            referer="",
            user_agent="tests",
            ip_address=ip_address,
        )
        InviteLead.objects.filter(pk=lead.pk).update(
            created_on=timezone.now() - datetime.timedelta(minutes=1)
        )

    response = client.post(
        reverse("pages:request-invite"),
        {
            "email": email,
            "comment": "",
            "timestamp": _timestamp_value(),
            "honeypot": "",
        },
        REMOTE_ADDR=ip_address,
    )

    assert response.status_code == 200
    assert InviteLead.objects.filter(email=email).count() == limit
    assert response.context["sent"] is False
    errors = response.context["form"].non_field_errors()
    assert pages_views.INVITATION_REQUEST_THROTTLE_MESSAGE in errors
