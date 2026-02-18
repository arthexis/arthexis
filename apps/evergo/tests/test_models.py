"""Tests for Evergo profile synchronization behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.evergo.models import EvergoUser


@pytest.mark.django_db
@patch("apps.evergo.models.requests.post")
def test_test_login_populates_remote_fields(mock_post):
    """Evergo login should persist the expected profile fields from the API payload."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite", email="suite@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 58642,
        "name": "Reginaldo Gutiérrez",
        "email": "reginaldocts@evergo.com",
        "two_factor_secret": None,
        "two_fa_enabled": 0,
        "two_fa_authenticated": 1,
        "created_at": "2025-12-11T18:18:48.000000Z",
        "updated_at": "2025-12-15T20:43:59.000000Z",
        "subempresas": [
            {
                "id": 25,
                "idInstalaEmpresa": 8,
                "nombre": "Reginaldo Gutiérrez",
            }
        ],
    }
    mock_post.return_value = mock_response

    result = profile.test_login()
    profile.refresh_from_db()

    assert result.response_code == 200
    assert profile.evergo_user_id == 58642
    assert profile.name == "Reginaldo Gutiérrez"
    assert profile.email == "reginaldocts@evergo.com"
    assert profile.empresa_id == 8
    assert profile.subempresa_id == 25
    assert profile.subempresa_name == "Reginaldo Gutiérrez"
    assert profile.two_fa_enabled is False
    assert profile.two_fa_authenticated is True
    assert profile.evergo_created_at is not None
    assert profile.evergo_updated_at is not None
    assert profile.last_login_test_at is not None
