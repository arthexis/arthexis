"""Regression coverage for shortcut management runtime and constraints."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.recipes.models import Recipe
from apps.shortcuts.constants import SHORTCUT_LISTENER_NODE_FEATURE_SLUG, SHORTCUT_MANAGEMENT_FEATURE_SLUG
from apps.shortcuts.models import ClipboardPattern, Shortcut
from apps.shortcuts.runtime import ensure_shortcut_listener_feature_enabled


@pytest.mark.django_db
def test_active_shortcut_key_combo_is_unique_across_kinds() -> None:
    """Regression: active key combos must remain globally unique across kinds."""

    recipe = Recipe.objects.create(slug="shortcut.recipe.1", display="Shortcut Recipe", script="result='ok'")
    Shortcut.objects.create(
        display="Server one",
        key_combo="CTRL+K",
        kind=Shortcut.Kind.SERVER,
        recipe=recipe,
        is_active=True,
    )

    duplicate = Shortcut(
        display="Client one",
        key_combo="CTRL+K",
        kind=Shortcut.Kind.CLIENT,
        recipe=recipe,
        is_active=True,
    )

    with pytest.raises(ValidationError):
        duplicate.full_clean(validate_constraints=True)


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


@pytest.mark.django_db
def test_server_shortcut_auto_enables_listener_assignment(monkeypatch) -> None:
    """Server shortcuts should auto-assign shortcut-listener when feature gate is enabled."""

    Feature.objects.update_or_create(
        slug=SHORTCUT_MANAGEMENT_FEATURE_SLUG,
        defaults={"display": "Shortcut Management", "is_enabled": True},
    )
    node_feature, _ = NodeFeature.objects.update_or_create(
        slug=SHORTCUT_LISTENER_NODE_FEATURE_SLUG,
        defaults={"display": "Shortcut Listener"},
    )
    node = Node.get_local()
    assert node is not None

    monkeypatch.setattr("apps.shortcuts.runtime.is_feature_active_for_node", lambda **kwargs: True)

    enabled = ensure_shortcut_listener_feature_enabled()

    assert enabled is True
    assert NodeFeatureAssignment.objects.filter(node=node, feature=node_feature).exists()
