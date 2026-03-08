from __future__ import annotations

import subprocess
import uuid
from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.recipes.admin import RecipeProductAdmin
from apps.recipes.models import Recipe, RecipeFormatDetectionError, RecipeProduct
from apps.recipes.utils import parse_recipe_arguments


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
    """Parser maps key-value tokens to keyword arguments."""

    token = str(uuid.uuid4())
    args, kwargs = parse_recipe_arguments(["alpha", "count=3", "--mode=fast", token])

    assert args == ["alpha", token]
    assert kwargs == {"count": "3", "mode": "fast"}


@pytest.mark.django_db
@pytest.mark.regression
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
@pytest.mark.regression
def test_execute_supports_bash_safe_normalized_kwarg_names(monkeypatch):
    """Regression: normalized bash kwarg env vars resolve across shell backends."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-normalize-{uuid.uuid4()}",
        display="Bash Normalize",
        body_type=Recipe.BodyType.BASH,
        script='echo "$RECIPE_KWARG_MODE_FAST-$RECIPE_KWARG__7FLAG"',
    )

    bash_failure = subprocess.CalledProcessError(
        1,
        ["bash", "-c", recipe.script],
        output="\x00RPC call\x00contains a handle to a WSL service\x00",
        stderr="",
    )

    def fake_run(command, **kwargs):
        shell = command[0]
        if shell == "bash":
            raise bash_failure
        if shell == "sh":
            env = kwargs["env"]
            normalized = f"{env['RECIPE_KWARG_MODE_FAST']}-{env['RECIPE_KWARG__7FLAG']}"
            return subprocess.CompletedProcess(command, 0, stdout=f"{normalized}\n", stderr="")
        raise AssertionError(f"Unexpected shell call: {command}")

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")
    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute(**{"mode-fast": "rapid", "7flag": "on"})

    assert execution.result == "rapid-on"


@pytest.mark.django_db
@pytest.mark.regression
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


@pytest.mark.regression
def test_is_windows_bash_launcher_failure_normalizes_nul_delimited_output(monkeypatch):
    """Regression: NUL-delimited launcher diagnostics are normalized before matching."""

    bash_failure = subprocess.CalledProcessError(
        1,
        ["bash", "-c", "echo ignored"],
        output="The\x00 RPC\tcall\x00contains  a\nhandle to a\x00WSL/service.",
        stderr="",
    )

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")

    assert Recipe._is_windows_bash_launcher_failure(bash_failure, shell="bash")


@pytest.mark.django_db
@pytest.mark.regression
def test_execute_supports_windows_git_bash_fallback(monkeypatch):
    """Regression: Windows recipes fall back to Git Bash paths when WSL bash fails."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-git-fallback-{uuid.uuid4()}",
        display="Bash Git Fallback",
        body_type=Recipe.BodyType.BASH,
        script='echo "$1-$RECIPE_KWARG_COLOR"',
    )

    monkeypatch.setenv("PROGRAMFILES", "D:/Tools")

    def fake_run(command, **_kwargs):
        shell = command[0]
        normalized_shell = shell.replace("\\", "/")
        if shell in {"bash", "sh"}:
            raise FileNotFoundError(f"{shell} missing")
        if normalized_shell == "D:/Tools/Git/bin/bash.exe":
            return subprocess.CompletedProcess(command, 0, stdout="hello-green\n", stderr="")
        raise AssertionError(f"Unexpected shell call: {command}")

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")
    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute("hello", color="green")

    assert execution.result == "hello-green"


@pytest.mark.django_db
@pytest.mark.regression
def test_execute_recognizes_missing_windows_shell_paths(monkeypatch):
    """Regression: missing Git/MSYS shell paths report runtime errors consistently."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-path-missing-{uuid.uuid4()}",
        display="Bash Path Missing",
        body_type=Recipe.BodyType.BASH,
        script='echo "never runs"',
    )

    def fake_run(command, **_kwargs):
        raise FileNotFoundError(f"{command[0]} missing")

    monkeypatch.setattr("apps.recipes.models.os.name", "nt")
    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="missing"):
        recipe.execute()


def test_shell_basename_handles_windows_paths_on_posix():
    """Windows-style shell paths normalize to executable names cross-platform."""

    assert Recipe._shell_basename(r"D:\Tools\Git\bin\bash.exe") == "bash.exe"
    assert Recipe._shell_basename("C:/msys64/usr/bin/sh.exe") == "sh.exe"


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
def test_execute_resolves_sigils_before_arg_substitution_for_bash(monkeypatch):
    """Arg values that look like sigils are not recursively resolved in bash mode."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"bash-sigil-order-{uuid.uuid4()}",
        display="Bash Sigil Order",
        body_type=Recipe.BodyType.BASH,
        script="echo [ARG.0]",
    )

    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.append(command[2])
        return subprocess.CompletedProcess(command, 0, stdout="[ENV.PATH]\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute("[ENV.PATH]")

    assert captured[0] == "echo '[ENV.PATH]'"
    assert execution.result == "[ENV.PATH]"


@pytest.mark.django_db
def test_execute_raises_for_unknown_body_type():
    """Unknown body types raise a format detection error."""

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

    with pytest.raises(RecipeFormatDetectionError, match="Unable to detect recipe format"):
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

    execution = recipe.execute("alpha", mode="fast")

    product = RecipeProduct.objects.get(recipe=recipe)
    assert execution.result == {"ok": True}
    assert product.input_args == ["alpha"]
    assert product.input_kwargs == {"mode": "fast"}
    assert product.result == '{"ok": true}'
    assert product.format_detected == Recipe.RecipeFormat.PYTHON


@pytest.mark.django_db
def test_execute_markdown_recipe_replaces_code_blocks(monkeypatch):
    """Markdown recipes render fenced block outputs inline."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    recipe = Recipe.objects.create(
        user=user,
        slug=f"guide-{uuid.uuid4()}.md",
        display="Guide",
        script="""# Demo\n\n```python\nresult = \"hello\"\n```\n\n```bash\necho done\n```\n""",
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    execution = recipe.execute()

    assert "hello" in execution.result
    assert "done" in execution.result
    assert "```" not in execution.result


@pytest.mark.django_db
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


@pytest.mark.parametrize("language", ["lua", "luajit"])
def test_execute_external_language_uses_lua_family_command(monkeypatch, language):
    """Lua/LuaJIT scripts execute through the correct interpreter command mapping."""

    recipe = Recipe(slug=f"{language}-check", display="Lua Check", script="")
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(command)
        env = kwargs["env"]
        assert env["RECIPE_ARGS_COUNT"] == "1"
        assert env["RECIPE_ARG_0"] == "semi;colon"
        assert env["RECIPE_KW_COLOR"] == "blue sky"
        assert env["RECIPE_KW_WEIRD_KEY"] == "value"
        return subprocess.CompletedProcess(command, 0, stdout=f"hello from {language}\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    result = recipe._execute_external_language(
        language=language,
        code=f'print("hello from {language}")',
        args=("semi;colon",),
        kwargs={"color": "blue sky", "weird-key": "value"},
    )

    assert result == f"hello from {language}"
    assert captured[0][:2] == [language, "-e"]


@pytest.mark.parametrize("language", ["lua", "luajit"])
def test_execute_external_language_reports_missing_interpreter(monkeypatch, language):
    """Lua/LuaJIT execution surfaces a clear interpreter-not-available error."""

    recipe = Recipe(slug=f"{language}-missing", display="Lua Missing", script="")

    def fake_run(command, **_kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match=f"interpreter '{language}' is not available"):
        recipe._execute_external_language(language=language, code='print("hello")', args=(), kwargs={})


def test_execute_external_language_does_not_template_arg_sigils(monkeypatch):
    """External interpreters keep arg sigils in code and receive args via env vars."""

    recipe = Recipe(slug="lua-sigils", display="Lua Sigils", script="")
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("apps.recipes.models.subprocess.run", fake_run)

    result = recipe._execute_language_block(
        language="lua",
        code='print("[ARG.0]")',
        args=('\"); os.execute("id"); --',),
        kwargs={},
        result_variable="result",
    )

    assert result == "ok"
    assert captured["command"] == ["lua", "-e", 'print("[ARG.0]")']
    assert captured["env"]["RECIPE_ARG_0"] == '"); os.execute("id"); --'

def test_recipe_product_admin_disables_delete_permission():
    """Recipe product admin remains read-only by denying delete permissions."""

    request = RequestFactory().get("/")
    admin_site = AdminSite()
    admin_instance = RecipeProductAdmin(RecipeProduct, admin_site)

    assert admin_instance.has_delete_permission(request) is False


@pytest.mark.django_db
def test_detect_format_infers_by_slug_extension():
    """Format detection uses slug extensions for single-language recipes."""

    user = get_user_model().objects.create(username=f"chef-{uuid.uuid4()}")
    python_recipe = Recipe.objects.create(
        user=user,
        slug=f"script-{uuid.uuid4()}.py",
        display="Python Script",
        body_type=Recipe.BodyType.BASH,
        script="result = 1",
    )
    bash_recipe = Recipe.objects.create(
        user=user,
        slug=f"script-{uuid.uuid4()}.sh",
        display="Bash Script",
        body_type=Recipe.BodyType.PYTHON,
        script='echo "ok"',
    )

    assert python_recipe.detect_format() == Recipe.RecipeFormat.PYTHON
    assert bash_recipe.detect_format() == Recipe.RecipeFormat.BASH
