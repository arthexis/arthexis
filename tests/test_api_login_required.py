"""Tests for the :func:`utils.api.api_login_required` decorator."""

from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse
from django.test import RequestFactory

from utils.api import api_login_required


def _dummy_view(request):
    return JsonResponse({"result": "ok"})


def test_api_login_required_returns_401_for_anonymous_user():
    """Anonymous users should receive a 401 JSON response."""

    wrapped_view = api_login_required(_dummy_view)
    request = RequestFactory().get("/api/protected/")
    request.user = AnonymousUser()

    response = wrapped_view(request)

    assert response.status_code == 401
    assert json.loads(response.content) == {"detail": "authentication required"}


def test_api_login_required_allows_authenticated_user():
    """Authenticated users should reach the wrapped view."""

    wrapped_view = api_login_required(_dummy_view)
    request = RequestFactory().get("/api/protected/")

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=f"api-user-{uuid.uuid4()}",
        email="api-user@example.com",
        password="password123",
    )
    request.user = user

    response = wrapped_view(request)

    assert response.status_code == 200
    assert json.loads(response.content) == {"result": "ok"}


def test_api_login_required_exposes_login_required_attribute():
    """Navigation helpers expect views to expose a ``login_required`` flag."""

    wrapped_view = api_login_required(_dummy_view)

    assert getattr(wrapped_view, "login_required") is True
