"""Services for desktop extension registration, execution, and shortcut sync."""

from __future__ import annotations

import base64
import ast
import json
import os
import shutil
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


@dataclass(slots=True)
class DesktopLaunchCapabilities:
    """Resolved desktop-launch capabilities derived from node feature state."""

    desktop_context_ready: bool
    systemd_control_available: bool
    browser_opener_available: bool
    browser_opener_command: str
    backend_port: int
    service_name: str

    @property
    def metadata_available(self) -> bool:
        """Whether enough feature metadata is available for managed launching."""

        return self.desktop_context_ready and self.browser_opener_available

    def as_dict(self) -> dict[str, object]:
        """Serialize capability values for command and shell callers."""

        return {
            "desktop_context_ready": self.desktop_context_ready,
            "systemd_control_available": self.systemd_control_available,
            "browser_opener_available": self.browser_opener_available,
            "browser_opener_command": self.browser_opener_command,
            "backend_port": self.backend_port,
            "service_name": self.service_name,
            "metadata_available": self.metadata_available,
        }


def resolve_browser_opener() -> str:
    """Resolve preferred command used to open desktop URLs."""

    for opener in ("firefox", "xdg-open", "sensible-browser"):
        if shutil.which(opener):
            return opener
    return ""


def read_backend_port(base_dir: Path, fallback: int = 8000) -> int:
    """Read backend port from ``.locks/backend_port.lck`` when present."""

    lock_file = base_dir / ".locks" / "backend_port.lck"
    try:
        value = lock_file.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    if value.isdigit() and 1 <= int(value) <= 65535:
        return int(value)
    return fallback


def read_service_name(base_dir: Path) -> str:
    """Read the configured service name from lock metadata when available."""

    lock_file = base_dir / ".locks" / "service.lck"
    try:
        return lock_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_desktop_launch_capabilities(*, base_dir: Path) -> DesktopLaunchCapabilities:
    """Resolve desktop-launch capabilities for local shortcut helpers."""

    local_node = Node.get_local()
    desktop_context_ready = False
    systemd_control_available = False
    if local_node is not None:
        desktop_context_ready = bool(
            local_node.has_feature("user-desktop") and is_desktop_ui_available()
        )
        systemd_control_available = bool(local_node.has_feature("systemd-manager"))
    opener_command = resolve_browser_opener()
    return DesktopLaunchCapabilities(
        desktop_context_ready=desktop_context_ready,
        systemd_control_available=systemd_control_available,
        browser_opener_available=bool(opener_command),
        browser_opener_command=opener_command,
        backend_port=read_backend_port(base_dir),
        service_name=read_service_name(base_dir),
    )


def serialize_desktop_launch_capabilities(*, base_dir: Path) -> str:
    """Return a JSON document with resolved desktop-launch capabilities."""

    return json.dumps(
        get_desktop_launch_capabilities(base_dir=base_dir).as_dict(), sort_keys=True
    )


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

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{extension.extension}"
    ) as ext_key:
        winreg.SetValueEx(ext_key, "", 0, winreg.REG_SZ, prog_id)

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}"
    ) as progid_key:
        winreg.SetValueEx(
            progid_key,
            "",
            0,
            winreg.REG_SZ,
            f"Arthexis handler for {extension.extension}",
        )

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        rf"Software\Classes\{prog_id}\shell\open\command",
    ) as command_key:
        winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command_value)

    return RegistrationResult(
        True, f"Registered {extension.extension} with command: {command_value}"
    )


def run_registered_extension(
    extension: RegisteredExtension, filename: str | None
) -> subprocess.CompletedProcess:
    """Execute the configured Django command for a registered extension."""

    command_parts, input_data = extension.build_runtime_command(filename)
    full_command = [
        sys.executable,
        str(Path(settings.BASE_DIR) / "manage.py"),
        *command_parts,
    ]
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
    """Evaluate a constrained boolean expression for install conditions."""

    if not expression:
        return True
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False

    allowed_nodes = (
        ast.Expression,
        ast.BoolOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.In,
        ast.NotIn,
        ast.Gt,
        ast.GtE,
        ast.Lt,
        ast.LtE,
        ast.Set,
        ast.Tuple,
        ast.List,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return False
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id != "has_feature":
                return False

    try:
        return bool(
            eval(
                compile(tree, "<shortcut-condition>", "eval"),
                {"__builtins__": {}},
                context,
            )
        )
    except (NameError, TypeError, ValueError):
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
        try:
            command_parts = shlex.split(shortcut.condition_command)
        except ValueError:
            return False
        if not command_parts:
            return False

        result = subprocess.run(
            command_parts,
            check=False,
            cwd=base_dir,
            env={
                **os.environ,
                "ARTHEXIS_SHORTCUT_SLUG": shortcut.slug,
                "ARTHEXIS_USERNAME": username,
            },
        )
        if result.returncode != 0:
            return False

    return True


def render_shortcut_desktop_entry(
    shortcut: DesktopShortcut, *, exec_value: str, icon_value: str
) -> str:
    """Render the .desktop file body for ``shortcut``."""

    def _sanitize_desktop_value(value: object) -> str:
        return str(value).replace("\r", " ").replace("\n", " ")

    categories = shortcut.categories or "Utility;"
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={_sanitize_desktop_value(shortcut.name)}",
        f"Comment={_sanitize_desktop_value(shortcut.comment)}",
        f"Exec={_sanitize_desktop_value(exec_value)}",
        f"Icon={_sanitize_desktop_value(icon_value)}",
        f"Terminal={'true' if shortcut.terminal else 'false'}",
        f"Categories={_sanitize_desktop_value(categories)}",
        f"StartupNotify={'true' if shortcut.startup_notify else 'false'}",
        "X-Arthexis-Managed=true",
    ]
    for key, value in sorted(shortcut.extra_entries.items()):
        lines.append(f"{_sanitize_desktop_value(key)}={_sanitize_desktop_value(value)}")
    return "\n".join(lines) + "\n"


def sync_desktop_shortcuts(
    *, base_dir: Path, username: str, port: int, remove_stale: bool = True
) -> DesktopSyncResult:
    """Synchronize desktop shortcut files from ``DesktopShortcut`` records."""

    desktop_dir = detect_desktop_dir(base_dir, username)
    if desktop_dir is None:
        return DesktopSyncResult(skipped=DesktopShortcut.objects.count())

    installed_files: set[Path] = set()
    result = DesktopSyncResult()

    shortcuts = DesktopShortcut.objects.prefetch_related(
        "required_features", "required_groups"
    )
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
        rendered = render_shortcut_desktop_entry(
            shortcut, exec_value=exec_value, icon_value=icon_value
        )
        target.write_text(rendered, encoding="utf-8")
        target.chmod(0o755)
        installed_files.add(target)
        result.installed += 1

    if remove_stale:
        for stale in desktop_dir.glob("*.desktop"):
            try:
                is_managed = "X-Arthexis-Managed=true" in stale.read_text(
                    encoding="utf-8"
                )
            except OSError:
                is_managed = False
            if not is_managed:
                continue
            if (
                stale not in installed_files
                and not DesktopShortcut.objects.filter(
                    desktop_filename=stale.stem
                ).exists()
            ):
                stale.unlink(missing_ok=True)
                result.removed += 1

    return result
