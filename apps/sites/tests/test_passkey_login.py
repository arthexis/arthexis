"""Tests for passkey login endpoints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from webauthn.helpers.exceptions import InvalidJSONStructure

from apps.users.models import PasskeyCredential

pytestmark = [pytest.mark.django_db]

@pytest.fixture
def user():
    """Create a user eligible for passkey login tests."""

    return get_user_model().objects.create_user(
        username="passkey-user",
        email="passkey@example.com",
        password="secret",
    )

@pytest.fixture
def passkey(user):
    """Create a persisted passkey credential bound to the test user."""

    return PasskeyCredential.objects.create(
        user=user,
        name="Laptop",
        credential_id="cred-1",
        public_key=b"test-public-key",
        sign_count=7,
        user_handle="user-handle",
    )

def test_passkey_login_options_sets_challenge_in_session(client, monkeypatch):
    """Options endpoint should issue public key options and persist challenge."""

    monkeypatch.setattr(
        "apps.sites.views.management.build_authentication_options",
        lambda request: SimpleNamespace(data={"challenge": "abc"}, challenge="session-challenge"),
    )

    response = client.post(reverse("pages:passkey-login-options"))

    assert response.status_code == 200
    assert response.json() == {"publicKey": {"challenge": "abc"}}
    assert client.session["passkey_login_challenge"] == "session-challenge"

def test_passkey_login_verify_authenticates_user(client, passkey, monkeypatch):
    """Verify endpoint should authenticate and update sign count after valid assertion."""

    session = client.session
    session["passkey_login_challenge"] = "expected-challenge"
    session.save()

    monkeypatch.setattr(
        "apps.sites.views.management.verify_authentication_response",
        lambda *args, **kwargs: SimpleNamespace(new_sign_count=9),
    )

    response = client.post(
        reverse("pages:passkey-login-verify"),
        data={
            "credential": {
                "id": passkey.credential_id,
                "rawId": "raw",
                "type": "public-key",
                "response": {
                    "clientDataJSON": "a",
                    "authenticatorData": "b",
                    "signature": "c",
                },
            },
            "next": "/release-checklist/",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/release-checklist/"

    passkey.refresh_from_db()
    assert passkey.sign_count == 9
    assert passkey.last_used_at is not None

    current_user = response.wsgi_request.user
    assert current_user.is_authenticated
    assert current_user.pk == passkey.user_id

def test_passkey_login_verify_rejects_missing_challenge(client):
    """Verify endpoint should reject requests without a stored challenge."""

    response = client.post(
        reverse("pages:passkey-login-verify"),
        data={"credential": {}},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["detail"]

def test_passkey_login_verify_rejects_unknown_credential(client):
    """Verify endpoint should reject credentials that are not registered."""

    session = client.session
    session["passkey_login_challenge"] = "expected-challenge"
    session.save()

    response = client.post(
        reverse("pages:passkey-login-verify"),
        data={
            "credential": {
                "id": "unknown",
                "rawId": "raw",
                "type": "public-key",
                "response": {
                    "clientDataJSON": "a",
                    "authenticatorData": "b",
                    "signature": "c",
                },
            }
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["detail"]

def test_passkey_login_verify_rejects_invalid_json_structure(client, passkey, monkeypatch):
    """Verify endpoint should reject malformed WebAuthn payload structures."""

    session = client.session
    session["passkey_login_challenge"] = "expected-challenge"
    session.save()

    def _raise_invalid(*args, **kwargs):
        raise InvalidJSONStructure("missing fields")

    monkeypatch.setattr(
        "apps.sites.views.management.verify_authentication_response",
        _raise_invalid,
    )

    response = client.post(
        reverse("pages:passkey-login-verify"),
        data={
            "credential": {
                "id": passkey.credential_id,
                "type": "public-key",
                "response": {},
            }
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["detail"]
