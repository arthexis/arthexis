"""Services for desktop extension registration, execution, and shortcut sync."""

from __future__ import annotations

import base64
import ast
import os
from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser

from apps.desktop.models import DesktopShortcut, RegisteredExtension
from apps.nodes.models import Node


class _ConditionExpressionEvaluator(ast.NodeVisitor):
    """Safely evaluate boolean shortcut expressions against a constrained context."""

    _ALLOWED_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)

    def __init__(self, context: dict[str, object]) -> None:
        self._context = context

    def evaluate(self, expression: str) -> bool:
        """Parse and evaluate a condition expression."""

        parsed = ast.parse(expression, mode="eval")
        return bool(self.visit(parsed.body))

    def visit_Name(self, node: ast.Name) -> object:  # noqa: N802
        if node.id not in self._context:
            raise ValueError(f"Unknown variable: {node.id}")
        return self._context[node.id]

    def visit_Constant(self, node: ast.Constant) -> object:  # noqa: N802
        return node.value

    def visit_Set(self, node: ast.Set) -> set[object]:  # noqa: N802
        return {self.visit(element) for element in node.elts}

    def visit_Tuple(self, node: ast.Tuple) -> tuple[object, ...]:  # noqa: N802
        return tuple(self.visit(element) for element in node.elts)

    def visit_List(self, node: ast.List) -> list[object]:  # noqa: N802
        return [self.visit(element) for element in node.elts]

    def visit_UnaryOp(self, node: ast.UnaryOp) -> bool:  # noqa: N802
        if isinstance(node.op, ast.Not):
            return not bool(self.visit(node.operand))
        raise ValueError("Unsupported unary operator in expression")

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:  # noqa: N802
        values = [bool(self.visit(value)) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise ValueError("Unsupported boolean operator in expression")

    def visit_Compare(self, node: ast.Compare) -> bool:  # noqa: N802
        left = self.visit(node.left)
        for operation, comparator in zip(node.ops, node.comparators, strict=True):
            if not isinstance(operation, self._ALLOWED_COMPARE_OPS):
                raise ValueError("Unsupported comparison operator in expression")
            right = self.visit(comparator)
            if isinstance(operation, ast.Eq) and left != right:
                return False
            if isinstance(operation, ast.NotEq) and left == right:
                return False
            if isinstance(operation, ast.In) and left not in right:
                return False
            if isinstance(operation, ast.NotIn) and left in right:
                return False
            left = right
        return True

    def visit_Call(self, node: ast.Call) -> bool:  # noqa: N802
        if not isinstance(node.func, ast.Name) or node.func.id != "has_feature":
            raise ValueError("Only has_feature(...) calls are allowed")
        if len(node.args) != 1 or node.keywords:
            raise ValueError("has_feature(...) expects exactly one positional argument")
        feature_slug = self.visit(node.args[0])
        if not isinstance(feature_slug, str):
            raise ValueError("has_feature(...) argument must be a string")
        has_feature = self._context.get("has_feature")
        if not callable(has_feature):
            raise ValueError("has_feature is not callable")
        return bool(has_feature(feature_slug))

    def generic_visit(self, node: ast.AST) -> object:
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")


@dataclass(slots=True)
class RegistrationResult:
    """Result of attempting an operating system extension registration."""

    success: bool
    message: str


@dataclass(slots=True)
class DesktopSyncResult:
    """Summarize the outcome of syncing desktop shortcuts."""

    installed: int = 0
    skipped: int = 0
    removed: int = 0


def build_windows_registry_command(extension: RegisteredExtension) -> str:
    """Build the Windows shell command used to open a file with this extension."""

    python_executable = Path(sys.executable)
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    return (
        f'"{python_executable}" "{manage_py}" desktop_extension_open '
        f'--extension-id {extension.pk} --filename "%1"'
    )


def register_extension_with_os(extension: RegisteredExtension) -> RegistrationResult:
    """Register extension mapping in the local operating system when supported."""

    if not extension.pk:
        return RegistrationResult(False, "Extension must be saved before registration.")

    if sys.platform != "win32":
        command_preview = build_windows_registry_command(extension)
        return RegistrationResult(
            True,
            (
                "Non-Windows host detected. Registration skipped; "
                f"command preview: {command_preview}"
            ),
        )

    import winreg  # type: ignore[import-not-found]

    prog_id = f"Arthexis.Desktop{extension.extension.lstrip('.').upper()}"
    command_value = build_windows_registry_command(extension)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{extension.extension}") as ext_key:
        winreg.SetValueEx(ext_key, "", 0, winreg.REG_SZ, prog_id)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{prog_id}") as progid_key:
        winreg.SetValueEx(progid_key, "", 0, winreg.REG_SZ, f"Arthexis handler for {extension.extension}")

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        fr"Software\Classes\{prog_id}\shell\open\command",
    ) as command_key:
        winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command_value)

    return RegistrationResult(True, f"Registered {extension.extension} with command: {command_value}")


def run_registered_extension(extension: RegisteredExtension, filename: str | None) -> subprocess.CompletedProcess:
    """Execute the configured Django command for a registered extension."""

    command_parts, input_data = extension.build_runtime_command(filename)
    full_command = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), *command_parts]
    return subprocess.run(
        full_command,
        input=(input_data or ""),
        text=True,
        capture_output=True,
        check=False,
    )


def detect_desktop_dir(base_dir: Path, username: str) -> Path | None:
    """Return the resolved Desktop directory for ``username`` when available."""

    base_dir = Path(base_dir).resolve()
    if not str(base_dir).startswith("/home/"):
        return None

    home_dir = Path("/home") / username
    if not home_dir.exists():
        return None

    desktop_candidate = home_dir / "Desktop"
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(home_dir)},
        )
        if result.returncode == 0 and result.stdout.strip():
            desktop_candidate = Path(result.stdout.strip())
    except FileNotFoundError:
        pass

    desktop_candidate.mkdir(parents=True, exist_ok=True)
    return desktop_candidate


def user_has_desktop_ui(base_dir: Path, username: str) -> bool:
    """Return whether the target user appears to have a usable desktop directory."""

    desktop_dir = detect_desktop_dir(base_dir, username)
    return bool(desktop_dir and desktop_dir.exists())


def is_desktop_ui_available() -> bool:
    """Return whether the local node appears capable of desktop UI shortcuts."""

    local_node = Node.get_local()
    if local_node is None:
        return False
    base_dir = Path(settings.BASE_DIR)
    username = base_dir.parts[2] if len(base_dir.parts) > 2 else ""
    if not username:
        return False
    return user_has_desktop_ui(base_dir, username)


def _resolve_user(username: str) -> AbstractBaseUser | None:
    """Fetch a Django user by username, returning ``None`` if missing."""

    UserModel = get_user_model()
    try:
        return UserModel.objects.get(username=username)
    except UserModel.DoesNotExist:
        return None


def _evaluate_expression(expression: str, context: dict[str, object]) -> bool:
    """Evaluate a restricted expression for install conditions."""

    if not expression:
        return True
    try:
        evaluator = _ConditionExpressionEvaluator(context)
        return evaluator.evaluate(expression)
    except (SyntaxError, TypeError, ValueError):
        return False


def _icon_value(shortcut: DesktopShortcut, username: str) -> str:
    """Resolve icon reference string for a shortcut desktop entry."""

    if shortcut.icon_base64:
        icon_dir = Path("/home") / username / ".local" / "share" / "icons" / "arthexis"
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_path = icon_dir / f"{shortcut.slug}.{shortcut.icon_extension.lstrip('.')}"
        icon_path.write_bytes(base64.b64decode(shortcut.icon_base64))
        return str(icon_path)
    if shortcut.icon_name:
        return shortcut.icon_name
    return "applications-system"


def _build_exec(shortcut: DesktopShortcut, port: int) -> str:
    """Build the desktop entry ``Exec`` string for a shortcut."""

    if shortcut.launch_mode == DesktopShortcut.LaunchMode.COMMAND:
        return shortcut.command

    target_url = shortcut.target_url.format(port=port)
    return shlex.join([sys.executable, "-m", "webbrowser", "-t", target_url])


def should_install_shortcut(
    shortcut: DesktopShortcut,
    *,
    base_dir: Path,
    username: str,
) -> bool:
    """Evaluate whether a shortcut should be installed for ``username``."""

    if not shortcut.is_enabled:
        return False

    has_desktop_ui = user_has_desktop_ui(base_dir, username)
    if shortcut.require_desktop_ui and not has_desktop_ui:
        return False

    local_node = Node.get_local()
    if local_node is None:
        return False

    missing_features = [
        feature.slug
        for feature in shortcut.required_features.all()
        if not local_node.has_feature(feature.slug)
    ]
    if missing_features:
        return False

    user = _resolve_user(username)
    user_groups = set(user.groups.values_list("name", flat=True)) if user else set()

    if shortcut.only_staff and (not user or not user.is_staff):
        return False
    if shortcut.only_superuser and (not user or not user.is_superuser):
        return False

    required_groups = set(shortcut.required_groups.values_list("name", flat=True))
    if required_groups and not required_groups.issubset(user_groups):
        return False

    expr_context = {
        "has_desktop_ui": has_desktop_ui,
        "is_staff": bool(user and user.is_staff),
        "is_superuser": bool(user and user.is_superuser),
        "group_names": user_groups,
        "has_feature": lambda slug: local_node.has_feature(slug),
    }
    if not _evaluate_expression(shortcut.condition_expression, expr_context):
        return False

    if shortcut.condition_command:
        command_parts = shlex.split(shortcut.condition_command)
        if not command_parts:
            return False
        result = subprocess.run(
            command_parts,
            check=False,
            cwd=base_dir,
            text=True,
            capture_output=True,
            env={**os.environ, "ARTHEXIS_SHORTCUT_SLUG": shortcut.slug, "ARTHEXIS_USERNAME": username},
        )
        if result.returncode != 0:
            return False

    return True


def render_shortcut_desktop_entry(shortcut: DesktopShortcut, *, exec_value: str, icon_value: str) -> str:
    """Render the .desktop file body for ``shortcut``."""

    def _sanitize_value(value: object) -> str:
        return str(value).replace("\n", " ").replace("\r", " ")

    categories = shortcut.categories or "Utility;"
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        "X-Arthexis-Managed=true",
        f"Name={_sanitize_value(shortcut.name)}",
        f"Comment={_sanitize_value(shortcut.comment)}",
        f"Exec={_sanitize_value(exec_value)}",
        f"Icon={_sanitize_value(icon_value)}",
        f"Terminal={'true' if shortcut.terminal else 'false'}",
        f"Categories={_sanitize_value(categories)}",
        f"StartupNotify={'true' if shortcut.startup_notify else 'false'}",
    ]
    for key, value in sorted(shortcut.extra_entries.items()):
        sanitized_key = _sanitize_value(key)
        if any(char in sanitized_key for char in ("=", "[", "]")):
            continue
        lines.append(f"{sanitized_key}={_sanitize_value(value)}")
    return "\n".join(lines) + "\n"


def sync_desktop_shortcuts(*, base_dir: Path, username: str, port: int, remove_stale: bool = True) -> DesktopSyncResult:
    """Synchronize desktop shortcut files from ``DesktopShortcut`` records."""

    desktop_dir = detect_desktop_dir(base_dir, username)
    if desktop_dir is None:
        return DesktopSyncResult(skipped=DesktopShortcut.objects.count())

    installed_files: set[Path] = set()
    result = DesktopSyncResult()

    shortcuts = DesktopShortcut.objects.prefetch_related("required_features", "required_groups")
    for shortcut in shortcuts:
        target = desktop_dir / f"{shortcut.desktop_filename}.desktop"
        if not should_install_shortcut(shortcut, base_dir=base_dir, username=username):
            result.skipped += 1
            if target.exists():
                target.unlink()
                result.removed += 1
            continue

        exec_value = _build_exec(shortcut, port)
        icon_value = _icon_value(shortcut, username)
        rendered = render_shortcut_desktop_entry(shortcut, exec_value=exec_value, icon_value=icon_value)
        target.write_text(rendered, encoding="utf-8")
        target.chmod(0o755)
        installed_files.add(target)
        result.installed += 1

    if remove_stale:
        managed_filenames = set(DesktopShortcut.objects.values_list("desktop_filename", flat=True))
        for stale in desktop_dir.glob("*.desktop"):
            if stale in installed_files or stale.stem in managed_filenames:
                continue
            try:
                stale_content = stale.read_text(encoding="utf-8")
            except OSError:
                continue
            if "X-Arthexis-Managed=true" not in stale_content and not stale.name.startswith("Arthexis"):
                continue
            stale.unlink(missing_ok=True)
            result.removed += 1

    return result
