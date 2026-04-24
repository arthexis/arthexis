"""Tests for manual JWT-backed general service token management."""

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.apis.admin import GeneralServiceTokenCreateForm
from apps.apis.models import GeneralServiceToken, GeneralServiceTokenEvent
from apps.groups.models import SecurityGroup


@pytest.fixture
def general_token_staff_user(db):
    return get_user_model().objects.create_user(
        username="general-token-operator",
        password="pass12345",
        is_staff=True,
    )


@pytest.mark.django_db
def test_general_service_token_create_requires_reveal_permission(client, general_token_staff_user):
    manage_permission = Permission.objects.get(codename="manage_general_service_tokens")
    general_token_staff_user.user_permissions.add(manage_permission)
    client.force_login(general_token_staff_user)

    response = client.post(
        reverse("admin:apis_generalservicetoken_create"),
        {
            "name": "Unrevealable token",
            "user_id": general_token_staff_user.id,
            "expires_in_days": 2,
            "security_group_ids": "",
            "custom_claims": {},
        },
    )

    assert response.status_code == 403
    assert not GeneralServiceToken.objects.filter(name="Unrevealable token").exists()


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
    assert GeneralServiceTokenEvent.objects.filter(
        token=expired,
        event_type=GeneralServiceTokenEvent.EventType.RETIRED,
        details__reason="expired",
    ).exists()


@pytest.mark.django_db
def test_general_service_token_create_form_reports_non_integer_security_group_ids():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="token-user-5", password="pass12345")
    form = GeneralServiceTokenCreateForm(
        data={
            "name": "Bad SG ids",
            "user_id": user.id,
            "expires_in_days": 2,
            "security_group_ids": "1,abc,2",
            "custom_claims": {},
        }
    )

    assert form.is_valid() is False
    assert "security_group_ids" in form.errors


@pytest.mark.django_db
def test_general_service_token_create_form_requires_custom_claims_object():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="token-user-7", password="pass12345")
    form = GeneralServiceTokenCreateForm(
        data={
            "name": "Bad claims",
            "user_id": user.id,
            "expires_in_days": 2,
            "security_group_ids": "",
            "custom_claims": ["not", "an", "object"],
        }
    )

    assert form.is_valid() is False
    assert "custom_claims" in form.errors


@pytest.mark.django_db
def test_authentication_handles_malformed_jwt_payload():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer-6", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user-6", password="pass12345")
    token, _ = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Malformed payload",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    header = GeneralServiceToken._urlsafe_b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    malformed_payload = GeneralServiceToken._urlsafe_b64(b"\xff")
    signing_input = f"{header}.{malformed_payload}".encode("utf-8")
    signature = GeneralServiceToken._urlsafe_b64(
        hmac.new(
            key=settings.SECRET_KEY.encode("utf-8"),
            msg=signing_input,
            digestmod="sha256",
        ).digest()
    )
    bad_payload_jwt = f"{header}.{malformed_payload}.{signature}"
    token.token_hash = hashlib.sha256(bad_payload_jwt.encode("utf-8")).hexdigest()
    token.save(update_fields=["token_hash", "updated_at"])

    authenticated, payload, error_code = GeneralServiceToken.authenticate_jwt(bad_payload_jwt)

    assert authenticated is None
    assert payload is None
    assert error_code == "token_signature_invalid"


@pytest.mark.django_db
def test_issue_general_service_token_uses_distinct_prefixes():
    user_model = get_user_model()
    actor = user_model.objects.create_user(username="issuer-8", password="pass12345", is_staff=True)
    target = user_model.objects.create_user(username="token-user-8", password="pass12345")

    first, _ = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Prefix token one",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    second, _ = GeneralServiceToken.issue(
        actor=actor,
        user=target,
        name="Prefix token two",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    assert first.token_prefix != second.token_prefix
    assert first.token_prefix.startswith("gst_")
    assert second.token_prefix.startswith("gst_")
