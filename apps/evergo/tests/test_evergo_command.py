"""Tests for the `evergo` management command."""

from __future__ import annotations

import io
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.evergo.models import EvergoUser


@pytest.mark.django_db
@patch("apps.evergo.models.requests.post")
def test_evergo_command_saves_credentials_and_tests_login(mock_post):
    """Command should save credentials, test login, and sync API fields."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-user", email="suite@example.com"
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 100,
        "name": "Suite Evergo",
        "email": "suite.evergo@example.com",
        "two_fa_enabled": 1,
        "two_fa_authenticated": 1,
        "created_at": "2025-01-01T00:00:00.000000Z",
        "updated_at": "2025-01-02T00:00:00.000000Z",
        "subempresas": [{"id": 5, "idInstalaEmpresa": 2, "nombre": "Ops"}],
    }
    mock_post.return_value = mock_response

    stdout = io.StringIO()
    call_command(
        "evergo",
        suite_user.username,
        "--email",
        "suite.evergo@example.com",
        "--password",
        "secret",
        "--test",
        stdout=stdout,
    )

    profile = EvergoUser.objects.get(user=suite_user)
    assert profile.evergo_email == "suite.evergo@example.com"
    assert profile.evergo_user_id == 100
    assert "Evergo login successful" in stdout.getvalue()


@pytest.mark.django_db
def test_evergo_command_reuses_existing_profile_when_duplicates_exist():
    """Command should update the oldest profile when duplicate rows exist."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-dup", email="suite-dup@example.com"
    )
    first = EvergoUser.objects.create(user=suite_user, evergo_email="old@example.com")
    EvergoUser.objects.create(user=suite_user, evergo_email="newer@example.com")

    call_command(
        "evergo",
        suite_user.username,
        "--email",
        "resolved@example.com",
        "--password",
        "secret",
    )

    first.refresh_from_db()
    assert first.evergo_email == "resolved@example.com"
    assert EvergoUser.objects.filter(user=suite_user).count() == 2


@pytest.mark.django_db
def test_evergo_command_raises_for_ambiguous_user_identifier():
    """Command should reject identifiers that match different users by username/email."""
    User = get_user_model()
    User.objects.create_user(username="admin@example.com", email="owner1@example.com")
    User.objects.create_user(username="owner2", email="admin@example.com")

    with pytest.raises(CommandError, match="matches multiple users"):
        call_command(
            "evergo",
            "admin@example.com",
            "--email",
            "suite.evergo@example.com",
            "--password",
            "secret",
        )
