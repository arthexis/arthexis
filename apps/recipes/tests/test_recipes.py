from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.recipes.models import Recipe, RecipeProduct


@pytest.mark.django_db
def test_execute_resolves_arg_sigils():
    """Recipe execution resolves positional and keyword argument sigils."""

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
def test_execute_supports_bash_body_type():
    """Regression: bash recipes execute successfully on Windows and POSIX shells."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-{uuid.uuid4()}",
        display="Bash Recipe",
        body_type=Recipe.BodyType.BASH,
        script='echo "$1-$RECIPE_KWARG_COLOR"',
    )

    execution = recipe.execute("hello", color="green")

    assert execution.result == "hello-green"


@pytest.mark.django_db
def test_execute_raises_for_failing_bash_script():
    """Bash recipe failures are surfaced as runtime errors."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-fail-{uuid.uuid4()}",
        display="Bash Failure",
        body_type=Recipe.BodyType.BASH,
        script='echo "bad" >&2\nexit 5',
    )

    with pytest.raises(RuntimeError, match="bad"):
        recipe.execute()


@pytest.mark.django_db
def test_execute_creates_recipe_product():
    """Every execution creates a persistent recipe product record."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"product-{uuid.uuid4()}",
        display="Product Recipe",
        script="result = {'ok': True}",
    )

    execution = recipe.execute("alpha", "beta", mode="fast")

    product = RecipeProduct.objects.get(recipe=recipe)
    assert execution.result == {"ok": True}
    assert product.input_args == [
        Recipe.PRODUCT_REDACTION_PLACEHOLDER,
        Recipe.PRODUCT_REDACTION_PLACEHOLDER,
    ]
    assert product.input_kwargs == {"mode": Recipe.PRODUCT_REDACTION_PLACEHOLDER}
    assert product.result == '{"ok": true}'
    assert product.format_detected == Recipe.RecipeFormat.PYTHON
    assert product.resolved_script == Recipe.PRODUCT_REDACTION_PLACEHOLDER
