"""Tests for the `evergo` management command."""

from __future__ import annotations

import io
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.evergo.models import EvergoUser


@pytest.mark.django_db
@patch("apps.evergo.models.requests.post")
def test_evergo_command_saves_credentials_and_tests_login(mock_post):
    """Command should save credentials, test login, and sync API fields."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-user", email="suite@example.com")

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
