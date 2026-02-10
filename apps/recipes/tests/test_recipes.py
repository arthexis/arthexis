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
@pytest.mark.parametrize(
    ("script", "expected_result"),
    [
        ("output = True", True),
        ("output = None", None),
    ],
)
def test_execute_honors_custom_result_variable(script, expected_result):
    """Recipe execution uses the configured result variable for truthy and empty values."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"result-{uuid.uuid4()}",
        display="Result Variable",
        script=script,
        result_variable="output",
    )

    execution = recipe.execute()

    assert execution.result == expected_result
    assert execution.result_variable == "output"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "script",
    [
        "result = 1 / 0",
        "result =",
        "import os\nresult = os.name",
    ],
)
def test_execute_raises_for_invalid_scripts(script):
    """Recipe execution rejects runtime, syntax, and import-driven script errors."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"invalid-{uuid.uuid4()}",
        display="Invalid Script",
        script=script,
    )

    with pytest.raises(RuntimeError):
        recipe.execute()


def test_parse_recipe_arguments_splits_kwargs():
    token = str(uuid.uuid4())
    args, kwargs = parse_recipe_arguments(["alpha", "count=3", "--mode=fast", token])

    assert args == ["alpha", token]
    assert kwargs == {"count": "3", "mode": "fast"}
