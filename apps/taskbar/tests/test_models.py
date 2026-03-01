"""Tests for taskbar menu and action model validation."""

from __future__ import annotations

import uuid

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.recipes.models import Recipe
from apps.taskbar.models import TaskbarMenu, TaskbarMenuAction


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action_type", "command", "recipe", "expected_error_field"),
    [
        (TaskbarMenuAction.ActionType.COMMAND, "", None, "command"),
        (TaskbarMenuAction.ActionType.COMMAND, "echo ok", "with-recipe", "recipe"),
        (TaskbarMenuAction.ActionType.RECIPE, "", None, "recipe"),
        (TaskbarMenuAction.ActionType.RECIPE, "echo should-not-exist", "with-recipe", "command"),
    ],
)
def test_taskbar_menu_action_invalid_combinations_raise_validation_error(
    action_type,
    command,
    recipe,
    expected_error_field,
):
    """Invalid command/recipe combinations should raise field-specific errors."""

    menu = TaskbarMenu.objects.create(name="Main", slug=f"main-{uuid.uuid4()}")
    assigned_recipe = None
    if recipe:
        user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
        assigned_recipe = Recipe.objects.create(
            user=user,
            slug=f"recipe-{uuid.uuid4()}",
            display="Recipe",
            script="result = 'ok'",
        )

    action = TaskbarMenuAction(
        menu=menu,
        label="Open",
        action_type=action_type,
        command=command,
        recipe=assigned_recipe,
    )

    with pytest.raises(ValidationError) as exc_info:
        action.full_clean()

    assert expected_error_field in exc_info.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action_type", "command", "include_recipe"),
    [
        (TaskbarMenuAction.ActionType.COMMAND, "echo hello", False),
        (TaskbarMenuAction.ActionType.RECIPE, "", True),
    ],
)
def test_taskbar_menu_action_valid_combinations_pass_validation(
    action_type,
    command,
    include_recipe,
):
    """Valid command/recipe combinations should pass model validation."""

    menu = TaskbarMenu.objects.create(name="Main", slug=f"main-{uuid.uuid4()}")
    assigned_recipe = None
    if include_recipe:
        user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
        assigned_recipe = Recipe.objects.create(
            user=user,
            slug=f"recipe-{uuid.uuid4()}",
            display="Recipe",
            script="result = 'ok'",
        )

    action = TaskbarMenuAction(
        menu=menu,
        label="Open",
        action_type=action_type,
        command=command,
        recipe=assigned_recipe,
    )

    action.full_clean()
