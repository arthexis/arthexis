from __future__ import annotations

from collections.abc import Iterable

import pytest
from django.urls import reverse

from gate_markers import gate

pytestmark = [pytest.mark.django_db, gate.upgrade]


def _response_contexts(response) -> Iterable:
    """Yield response contexts in a normalized iterable form."""

    contexts = response.context
    if contexts is None:
        return []
    if isinstance(contexts, list):
        return contexts
    return [contexts]


def _assert_username_field_present(response) -> None:
    assert response.status_code == 200

    for context in _response_contexts(response):
        form = context.get("form") if hasattr(context, "get") else None
        if form is not None and "username" in form.fields:
            return

    raise AssertionError(
        "Expected a login form exposing a username field in response context."
    )


def test_public_login_route_renders(client):
    response = client.get(reverse("pages:login"))

    _assert_username_field_present(response)


def test_admin_login_route_renders(client):
    response = client.get(reverse("admin:login"))

    _assert_username_field_present(response)
