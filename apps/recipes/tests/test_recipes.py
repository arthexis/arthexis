from __future__ import annotations

import subprocess
import uuid
from unittest import mock

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
def test_execute_supports_bash_safe_normalized_kwarg_names():
    """Regression: normalized bash kwarg env vars resolve across shell backends."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-normalize-{uuid.uuid4()}",
        display="Bash Normalize",
        body_type=Recipe.BodyType.BASH,
        script='echo "$RECIPE_KWARG_MODE_FAST-$RECIPE_KWARG__7FLAG"',
    )

    execution = recipe.execute(**{"mode-fast": "rapid", "7flag": "on"})

    assert execution.result == "rapid-on"


@pytest.mark.django_db
def test_execute_supports_windows_bash_launcher_fallback(monkeypatch):
    """Regression: Windows WSL bash launcher failures fall back to sh."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-fallback-{uuid.uuid4()}",
        display="Bash Fallback",
        body_type=Recipe.BodyType.BASH,
        script='echo "$1-$RECIPE_KWARG_COLOR"',
    )

    bash_failure = subprocess.CalledProcessError(
        1,
        ["bash", "-c", recipe.script],
        output="The RPC call contains a handle to a WSL service.",
        stderr="",
    )

    def fake_run(command, **_kwargs):
        shell = command[0]
        if shell == "bash":
            raise bash_failure
        if shell == "sh":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="hello-green\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected shell call: {command}")

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")
    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute("hello", color="green")

    assert execution.result == "hello-green"


@pytest.mark.django_db
def test_execute_raises_for_unknown_body_type():
    """Unknown body types raise a runtime error instead of silently falling back."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"unknown-body-{uuid.uuid4()}",
        display="Unknown Body",
        body_type=Recipe.BodyType.PYTHON,
        script='result = "ok"',
    )

    Recipe.objects.filter(pk=recipe.pk).update(body_type="unknown")
    recipe.refresh_from_db()

    with pytest.raises(RuntimeError, match="Unsupported recipe body type"):
        recipe.execute()


@pytest.mark.django_db
def test_execute_raises_runtime_error_for_bash_os_failures():
    """Bash startup failures are surfaced as runtime errors."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-oserror-{uuid.uuid4()}",
        display="Bash OS Error",
        body_type=Recipe.BodyType.BASH,
        script='echo "never runs"',
    )

    with (
        mock.patch("apps.recipes.models.subprocess.run", side_effect=FileNotFoundError("bash missing")),
        pytest.raises(RuntimeError, match="bash missing"),
    ):
        recipe.execute()
