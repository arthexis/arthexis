from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.users.models import PasskeyCredential

pytestmark = pytest.mark.django_db


def test_passkey_admin_register_start_sets_session(admin_client):
    user = get_user_model().objects.create_user(
        username="passkey-admin-target",
        email="passkey-target@example.com",
        password="secret",
    )

    response = admin_client.post(
        reverse("admin:users_passkeycredential_register"),
        data={"start": "1", "user": str(user.pk), "name": "Office Key"},
    )

    assert response.status_code == 200
    pending = admin_client.session["users_admin_passkey_registration"]
    assert pending["challenge"]
    assert pending["name"] == "Office Key"
    assert pending["user_id"] == user.pk


def test_passkey_admin_register_start_escapes_options_json(admin_client):
    user = get_user_model().objects.create_user(
        username='bad </script><script>alert("x")</script>',
        email="xss-target@example.com",
        password="secret",
    )

    response = admin_client.post(
        reverse("admin:users_passkeycredential_register"),
        data={"start": "1", "user": str(user.pk), "name": "Office Key"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="passkey-public-key-options"' in content
    assert "</script><script>alert(" not in content
    assert "\\u003C/script\\u003E\\u003Cscript\\u003Ealert" in content


def test_passkey_admin_register_finish_creates_passkey(admin_client, monkeypatch):
    user = get_user_model().objects.create_user(
        username="passkey-admin-target",
        email="passkey-target@example.com",
        password="secret",
    )
    session = admin_client.session
    session["users_admin_passkey_registration"] = {
        "challenge": "expected-challenge",
        "name": "Office Key",
        "user_handle": "user-handle",
        "user_id": user.pk,
    }
    session.save()

    monkeypatch.setattr(
        "apps.users.admin.verify_registration_response",
        lambda *args, **kwargs: SimpleNamespace(
            credential_id=b"cred-1",
            credential_public_key=b"public-key",
            sign_count=3,
        ),
    )

    response = admin_client.post(
        reverse("admin:users_passkeycredential_register"),
        data={
            "finish": "1",
            "credential_json": '{"response":{"transports":["internal"]}}',
        },
        follow=True,
    )

    assert response.status_code == 200
    passkey = PasskeyCredential.objects.get(user=user, name="Office Key")
    assert passkey.sign_count == 3
    assert passkey.transports == ["internal"]
    assert "users_admin_passkey_registration" not in admin_client.session
