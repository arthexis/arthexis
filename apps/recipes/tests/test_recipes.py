from __future__ import annotations

import subprocess
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


@pytest.mark.django_db
def test_execute_escapes_bash_arg_sigils(monkeypatch):
    """Bash arg sigils are shell-escaped to avoid command injection."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-arg-escape-{uuid.uuid4()}",
        display="Bash Arg Escape",
        body_type=Recipe.BodyType.BASH,
        script="echo [ARG.0]",
    )

    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.append(command[2])
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute("hello; cat /etc/passwd")

    assert execution.result == "ok"
    assert captured[0] == "echo 'hello; cat /etc/passwd'"


@pytest.mark.django_db
def test_execute_windows_bash_launcher_falls_back_to_sh(monkeypatch):
    """Windows bash launcher failures should fall back to the next shell candidate."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-win-fallback-{uuid.uuid4()}",
        display="Windows Fallback",
        body_type=Recipe.BodyType.BASH,
        script="echo ok",
    )

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")
    monkeypatch.setattr(
        Recipe, "_bash_shell_candidates", staticmethod(lambda: ("bash", "sh"))
    )

    def _fake_run(command, **_kwargs):
        shell = command[0]
        if shell == "bash":
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                output="",
                stderr="WSL\x00service\x00createinstance\x00RPC\x00call\x00failed",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", _fake_run)

    execution = recipe.execute()

    assert execution.result == "ok"


@pytest.mark.django_db
@pytest.mark.pr_origin(6217)
def test_execute_markdown_bash_blocks_quote_arg_sigils(monkeypatch):
    """Markdown bash fences shell-escape arg sigils before execution."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"guide-bash-{uuid.uuid4()}.md",
        display="Guide Bash",
        script="""```bash\necho [ARG.0]\n```""",
    )

    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.append(command[2])
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute("hello; cat /etc/passwd")

    assert execution.result == "ok"
    assert captured[0] == "echo 'hello; cat /etc/passwd'"
