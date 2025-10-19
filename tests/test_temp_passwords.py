from __future__ import annotations

import io
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.management import call_command
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.test import Client, RequestFactory
from django.urls import reverse

from core import temp_passwords
from core.backends import TempPasswordBackend


def test_temp_password_command_creates_lockfile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(temp_passwords, "generate_password", lambda length=16: "TempPass123")

    User = get_user_model()
    user = User.objects.create_user(username="alice", email="alice@example.com", password="irrelevant")

    output = io.StringIO()
    call_command("temp_password", "alice", stdout=output)

    message = output.getvalue()
    assert "TempPass123" in message

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["username"] == "alice"
    assert data["password_hash"] != "TempPass123"
    assert data.get("allow_change") is False
    expires_at = parse_datetime(data["expires_at"])
    assert expires_at is not None
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at)
    delta = expires_at - timezone.now()
    assert timedelta(minutes=59) <= delta <= timedelta(hours=1, minutes=1)


def test_temp_password_command_allows_change_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(temp_passwords, "generate_password", lambda length=16: "TempPass456")

    User = get_user_model()
    user = User.objects.create_user(username="alex", email="alex@example.com", password="irrelevant")

    output = io.StringIO()
    call_command("temp_password", "alex", allow_change=True, stdout=output)

    message = output.getvalue()
    assert "TempPass456" in message
    assert "old password requirement" in message

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["username"] == user.username
    assert data.get("allow_change") is True


def test_temp_password_backend_authenticates_and_activates(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="bob", password="old-password")
    user.is_active = False
    user.save(update_fields=["is_active"])

    password = "TempPass123"
    expires_at = timezone.now() + timedelta(minutes=10)
    temp_passwords.store_temp_password(user.username, password, expires_at)

    backend = TempPasswordBackend()
    authenticated = backend.authenticate(None, username="bob", password=password)

    assert authenticated is not None
    assert authenticated.pk == user.pk
    authenticated.refresh_from_db()
    assert authenticated.is_active
    files = list(tmp_path.iterdir())
    assert len(files) == 1


def test_temp_password_form_login_reactivates_inactive_user(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="frank", password="irrelevant", is_staff=True)
    user.is_active = False
    user.save(update_fields=["is_active"])

    password = "TempPass123"
    expires_at = timezone.now() + timedelta(minutes=10)
    temp_passwords.store_temp_password(user.username, password, expires_at)

    request = RequestFactory().post("/admin/login/")
    form = AuthenticationForm(request=request, data={"username": user.username, "password": password})

    assert form.is_valid()
    authenticated = form.get_user()
    assert authenticated is not None
    assert authenticated.pk == user.pk
    authenticated.refresh_from_db()
    assert authenticated.is_active


def test_temp_password_backend_rejects_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="carol", password="old-password")

    password = "TempPass123"
    expires_at = timezone.now() - timedelta(minutes=5)
    entry = temp_passwords.store_temp_password(user.username, password, expires_at)

    backend = TempPasswordBackend()
    result = backend.authenticate(None, username="carol", password=password)

    assert result is None
    assert not entry.path.exists()


def test_user_check_password_accepts_temp_password(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="dave", password="old-password")

    password = "TempPass123"
    expires_at = timezone.now() + timedelta(minutes=10)
    temp_passwords.store_temp_password(
        user.username,
        password,
        expires_at,
        allow_change=True,
    )

    assert user.check_password("old-password")
    assert user.check_password(password)


def test_user_check_password_rejects_temp_password_without_allow_change(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="ella", password="old-password")

    password = "TempPass789"
    expires_at = timezone.now() + timedelta(minutes=10)
    temp_passwords.store_temp_password(user.username, password, expires_at)

    assert not user.check_password(password)


def test_user_set_password_discards_temp_password(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(username="erin", password="old-password")

    password = "TempPass123"
    expires_at = timezone.now() + timedelta(minutes=10)
    entry = temp_passwords.store_temp_password(
        user.username,
        password,
        expires_at,
        allow_change=True,
    )

    assert entry.path.exists()
    user.set_password("new-password")
    user.save(update_fields=["password"])

    assert not entry.path.exists()
    assert not user.check_password(password)


def test_request_temp_password_view_generates_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_PASSWORD_LOCK_DIR", str(tmp_path), raising=False)

    User = get_user_model()
    user = User.objects.create_user(
        username="zoe",
        password="irrelevant",
        is_staff=True,
    )

    client = Client()
    client.force_login(user)
    response = client.get(reverse("admin-request-temp-password"))

    assert response.status_code == 200
    response.render()
    context = response.context_data
    password = context["password"]
    assert isinstance(password, str) and password

    entry = temp_passwords.load_temp_password(user.username)
    assert entry is not None
    assert entry.allow_change
    assert entry.check_password(password)
    assert context["allow_change"] is True
    assert context["return_url"] == reverse("admin:password_change")
