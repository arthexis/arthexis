from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.recipes.models import Recipe
from apps.recipes.utils import parse_recipe_arguments


@pytest.mark.django_db
def test_execute_resolves_arg_sigils():
    user = get_user_model().objects.create(username="chef")
    recipe = Recipe.objects.create(
        user=user,
        slug="greet",
        display="Greeter",
        script="result = '[ARG.0]-[ARG.color]'",
    )

    execution = recipe.execute("hello", color="blue")

    assert execution.result == "hello-blue"


@pytest.mark.django_db
def test_execute_honors_result_variable():
    user = get_user_model().objects.create(username="chef-2")
    recipe = Recipe.objects.create(
        user=user,
        slug="flag",
        display="Flag Recipe",
        script="output = True",
        result_variable="output",
    )

    execution = recipe.execute()

    assert execution.result is True
    assert execution.result_variable == "output"


def test_parse_recipe_arguments_splits_kwargs():
    token = str(uuid.uuid4())
    args, kwargs = parse_recipe_arguments(["alpha", "count=3", "--mode=fast", token])

    assert args == ["alpha", token]
    assert kwargs == {"count": "3", "mode": "fast"}
