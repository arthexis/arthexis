"""Tests for self-service token management authorization and lifecycle integrity."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.apis.models import ServiceToken, ServiceTokenEvent


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="token-operator",
        password="pass12345",
        is_staff=True,
    )


@pytest.mark.django_db
def test_create_token_requires_manage_permission(client, staff_user):
    """Staff users without explicit token permissions should be blocked."""

    client.force_login(staff_user)
    response = client.get(reverse("admin:apis_servicetoken_create"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_token_secret_reveal_is_one_time_and_audited(client, staff_user):
    """Created token secret should only be shown once and must emit an audit event."""

    permissions = Permission.objects.filter(
        codename__in=["manage_service_tokens", "reveal_service_token_secret"]
    )
    staff_user.user_permissions.add(*permissions)
    client.force_login(staff_user)

    create_response = client.post(
        reverse("admin:apis_servicetoken_create"),
        {
            "name": "OCPP Partner",
            "scopes": "ocpp.read,ocpp.write",
            "expires_in_days": 10,
        },
    )

    assert create_response.status_code == 302
    token = ServiceToken.objects.get(name="OCPP Partner")

    reveal_url = reverse("admin:apis_servicetoken_reveal", args=[token.pk])
    first_reveal = client.get(reveal_url)
    second_reveal = client.get(reveal_url)

    assert first_reveal.status_code == 200
    assert "atk_" in first_reveal.content.decode("utf-8")
    assert "no longer available" in second_reveal.content.decode("utf-8")
    assert ServiceTokenEvent.objects.filter(
        token=token,
        event_type=ServiceTokenEvent.EventType.REVEALED,
    ).count() == 1


@pytest.mark.django_db
def test_rotate_token_replaces_old_token_and_records_events(client, staff_user):
    """Rotate action should inactivate previous token and create a replacement token."""

    permissions = Permission.objects.filter(
        codename__in=["manage_service_tokens", "reveal_service_token_secret"]
    )
    staff_user.user_permissions.add(*permissions)
    client.force_login(staff_user)

    token, _ = ServiceToken.issue(
        actor=staff_user,
        name="Control API",
        scopes=["nodes.read"],
        expires_at=timezone.now() + timedelta(days=12),
    )

    rotate_response = client.post(
        reverse("admin:apis_servicetoken_rotate", args=[token.pk]),
        {"reason": "Routine rotation", "impact_note": "Update downstream clients."},
    )

    assert rotate_response.status_code == 302
    token.refresh_from_db()
    replacement = ServiceToken.objects.get(rotated_from=token)

    assert token.status == ServiceToken.Status.REPLACED
    assert replacement.status == ServiceToken.Status.ACTIVE
    assert ServiceTokenEvent.objects.filter(
        token=token,
        event_type=ServiceTokenEvent.EventType.ROTATED,
    ).exists()


@pytest.mark.django_db
def test_revoke_token_marks_inactive_and_writes_audit_event(client, staff_user):
    """Revoke action should update token status and persist reason in audit details."""

    manage_permission = Permission.objects.get(codename="manage_service_tokens")
    staff_user.user_permissions.add(manage_permission)
    client.force_login(staff_user)

    token, _ = ServiceToken.issue(
        actor=staff_user,
        name="Ops API",
        scopes=["ops.read"],
        expires_at=timezone.now() + timedelta(days=5),
    )

    response = client.post(
        reverse("admin:apis_servicetoken_revoke", args=[token.pk]),
        {"reason": "Compromise suspected", "impact_note": "Clients need a replacement."},
    )

    assert response.status_code == 302
    token.refresh_from_db()
    assert token.status == ServiceToken.Status.REVOKED
    revoke_event = ServiceTokenEvent.objects.get(
        token=token,
        event_type=ServiceTokenEvent.EventType.REVOKED,
    )
    assert revoke_event.details["reason"] == "Compromise suspected"
