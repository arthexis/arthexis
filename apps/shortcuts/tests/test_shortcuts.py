"""Regression coverage for shortcut execution user flows."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.features.models import Feature
from apps.recipes.models import Recipe
from apps.shortcuts.constants import SHORTCUT_MANAGEMENT_FEATURE_SLUG
from apps.shortcuts.models import ClipboardPattern, Shortcut


@pytest.mark.django_db
def test_client_shortcut_executes_first_matching_clipboard_pattern(client) -> None:
    """Client shortcuts should execute the first matching clipboard pattern recipe."""

    feature, _ = Feature.objects.update_or_create(
        slug=SHORTCUT_MANAGEMENT_FEATURE_SLUG,
        defaults={"display": "Shortcut Management", "is_enabled": True},
    )
    assert feature.is_enabled

    user_model = get_user_model()
    user = user_model.objects.create_user(username="shortcut-user", password="password", is_staff=True)
    client.force_login(user)

    fallback_recipe = Recipe.objects.create(
        slug="shortcut.fallback",
        display="Fallback",
        script="result = kwargs.get('clipboard', '') + '-fallback'",
    )
    primary_recipe = Recipe.objects.create(
        slug="shortcut.primary",
        display="Primary",
        script="result = kwargs.get('clipboard', '') + '-pattern'",
    )
    shortcut = Shortcut.objects.create(
        display="Clipboard shortcut",
        key_combo="CTRL+SHIFT+V",
        kind=Shortcut.Kind.CLIENT,
        recipe=fallback_recipe,
        use_clipboard_patterns=True,
        is_active=True,
        clipboard_output_enabled=True,
    )
    ClipboardPattern.objects.create(
        shortcut=shortcut,
        display="ticket",
        pattern=r"^TKT-",
        priority=1,
        recipe=primary_recipe,
        is_active=True,
        clipboard_output_enabled=True,
    )

    response = client.post(
        reverse("shortcuts:client-execute", args=[shortcut.pk]),
        data=json.dumps({"clipboard": "TKT-10"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recipe"] == "shortcut.primary"
    assert payload["clipboard_output"] == "TKT-10-pattern"
    assert payload["matched_pattern_id"] is not None
