"""Tests for manual JWT-backed general service token management."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.apis.models import GeneralServiceToken, GeneralServiceTokenEvent
from apps.groups.models import SecurityGroup


@pytest.mark.django_db
def test_issue_general_service_token_includes_expected_claims():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user", password="pass12345")
    group = SecurityGroup.objects.create(name="Ops")
    target.groups.add(group)

    expires_at = timezone.now() + timedelta(hours=2)
    token, raw_jwt = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Manual API JWT",
        expires_at=expires_at,
        security_groups=[group],
        claims={"aud": "partner-api"},
    )

    authenticated, payload, error_code = GeneralServiceToken.authenticate_jwt(raw_jwt)

    assert error_code == ""
    assert authenticated == token
    assert payload["sub"] == str(target.pk)
    assert payload["sg_ids"] == [group.id]
    assert payload["aud"] == "partner-api"
    assert GeneralServiceTokenEvent.objects.filter(
        token=token,
        event_type=GeneralServiceTokenEvent.EventType.CREATED,
    ).exists()


@pytest.mark.django_db
def test_security_group_filter_requires_user_membership():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer-2", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user-2", password="pass12345")
    allowed_group = SecurityGroup.objects.create(name="Allowed")
    denied_group = SecurityGroup.objects.create(name="Denied")
    target.groups.add(allowed_group)

    token, _ = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="SG restricted",
        expires_at=timezone.now() + timedelta(hours=1),
        security_groups=[allowed_group],
    )

    assert token.can_access_security_group(allowed_group.id) is True
    assert token.can_access_security_group(denied_group.id) is False


@pytest.mark.django_db
def test_authentication_retires_expired_general_service_token():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer-3", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user-3", password="pass12345")

    token, raw_jwt = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Soon expired",
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    token.expires_at = timezone.now() - timedelta(seconds=10)
    token.save(update_fields=["expires_at", "updated_at"])

    authenticated, payload, error_code = GeneralServiceToken.authenticate_jwt(raw_jwt)

    token.refresh_from_db()
    assert authenticated is None
    assert payload is None
    assert error_code == "token_expired"
    assert token.status == GeneralServiceToken.Status.RETIRED
    assert GeneralServiceTokenEvent.objects.filter(
        token=token,
        event_type=GeneralServiceTokenEvent.EventType.RETIRED,
    ).exists()


@pytest.mark.django_db
def test_retire_general_service_tokens_command_marks_expired_tokens_retired():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer-4", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user-4", password="pass12345")

    expired, _ = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Expired for command",
        expires_at=timezone.now() + timedelta(minutes=2),
    )
    GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Still active",
        expires_at=timezone.now() + timedelta(days=1),
    )
    expired.expires_at = timezone.now() - timedelta(minutes=1)
    expired.save(update_fields=["expires_at", "updated_at"])

    call_command("retire_general_service_tokens")

    expired.refresh_from_db()
    assert expired.status == GeneralServiceToken.Status.RETIRED
