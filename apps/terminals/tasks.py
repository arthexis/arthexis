from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from celery import shared_task
from django.conf import settings

from .models import AgentTerminal


def _has_desktop_ui() -> bool:
    return bool(getattr(settings, "DESKTOP_UI_ENABLED", False) or getattr(settings, "DESKTOP_UI", False))


def _is_windows() -> bool:
    return os.name == "nt"


def _terminal_state_dir() -> Path:
    override = os.environ.get("ARTHEXIS_TERMINAL_STATE_DIR")
    if override:
        return Path(override)
    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        base = Path(local_app_data) / "Arthexis"
    else:
        configured_state_home = os.environ.get("XDG_STATE_HOME")
        base = Path(configured_state_home) if configured_state_home else Path.home() / ".local" / "state"
        if not _can_create_state_dir(base):
            return Path(os.environ.get("TMPDIR") or "/tmp") / "arthexis-agent-terminals"
    return base / "agent-terminals"


def _can_create_state_dir(base: Path) -> bool:
    current = base
    while not current.exists() and current.parent != current:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK | os.X_OK)


def _terminal_pid_file(terminal_pk: int) -> Path:
    return _terminal_state_dir() / f"{terminal_pk}.pid"


def _named_terminal_pid_file(state_key: str) -> Path:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", state_key).strip(".-")
    return _terminal_state_dir() / f"{safe_key or 'terminal'}.pid"


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError, SystemError):
        return False
    return True


def _read_pid_file(pid_file: Path) -> tuple[int | None, str | None]:
    if not pid_file.exists():
        return None, None
    try:
        pid, command = (pid_file.read_text().splitlines() + [""])[:2]
        return int(pid.strip()), command.strip() or None
    except (OSError, TypeError, ValueError):
        return None, None


def _process_commandline(pid: int) -> str:
    if os.name == "nt":
        return ""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.exists():
        return ""
    try:
        return proc_cmdline.read_bytes().decode(errors="ignore").replace("\x00", " ").strip()
    except OSError:
        return ""


def _terminal_running(pid_file: Path) -> bool:
    pid, expected_command = _read_pid_file(pid_file)
    if not pid or pid <= 0:
        return False
    try:
        if not _is_process_running(pid):
            raise subprocess.CalledProcessError(1, ["kill", "-0", str(pid)])
    except (OSError, subprocess.CalledProcessError):
        pid_file.unlink(missing_ok=True)
        return False

    if expected_command and os.name != "nt":
        current_command = _process_commandline(pid)
        if not current_command or expected_command not in current_command:
            pid_file.unlink(missing_ok=True)
            return False

    return True


def _build_startup_script(terminal: AgentTerminal) -> str:
    lines = [terminal.resolved_launch_command(), terminal.resolved_launch_prompt()]
    blocks = terminal.resolved_prompt_blocks()
    if terminal.prompt_block_mode == AgentTerminal.LOOP_REPEAT and blocks:
        block_body = "\n".join(block.strip() for block in blocks if block.strip())
        if block_body:
            lines.append("while true; do")
            lines.append(block_body)
            lines.append("done")
    else:
        lines.extend(blocks)

    script_lines: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        script_lines.append(text)
    return "\n".join(script_lines)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _split_windows_command(value: str) -> list[str]:
    return [part.strip("\"") for part in shlex.split(value, posix=False)]


def _command_script(
    command: Sequence[str],
    *,
    working_directory: Path | str | None,
    shell: str,
) -> str:
    if not command:
        raise ValueError("Terminal command cannot be empty.")
    if shell == "powershell":
        lines = []
        if working_directory:
            lines.append(f"Set-Location -LiteralPath {_powershell_quote(str(working_directory))}")
        executable, *args = [str(part) for part in command]
        joined_args = " ".join(_powershell_quote(arg) for arg in args)
        suffix = f" {joined_args}" if joined_args else ""
        lines.append(f"& {_powershell_quote(executable)}{suffix}")
        return "\n".join(lines)
    lines = []
    if working_directory:
        lines.append(f"cd {shlex.quote(str(working_directory))}")
    lines.append(shlex.join(str(part) for part in command))
    return "\n".join(lines)


def _write_windows_startup_script(state_key: str, startup_script: str) -> Path:
    script_dir = _terminal_state_dir() / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', state_key).strip('.-') or 'terminal'}.ps1"
    script_path.write_text(startup_script, encoding="utf-8")
    return script_path


def _windows_terminal_command(
    *,
    script_path: Path,
    title: str,
    executable: str = "",
) -> list[str]:
    terminal = executable.strip()
    if not terminal or terminal == "x-terminal-emulator":
        terminal_path = shutil.which("wt.exe") or shutil.which("wt") or ""
        terminal_parts = [terminal_path] if terminal_path else []
    else:
        terminal_parts = _split_windows_command(terminal)
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    if terminal_parts:
        return [
            *terminal_parts,
            "new-tab",
            "--title",
            title,
            powershell,
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
    return [
        powershell,
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]


def _launch_startup_script(
    startup_script: str,
    *,
    executable: str = "",
    title: str = "Arthexis Agent Terminal",
    state_key: str = "terminal",
) -> Path:
    pid_dir = _terminal_state_dir()
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _named_terminal_pid_file(state_key)
    if _is_windows():
        script_path = _write_windows_startup_script(state_key, startup_script)
        command = _windows_terminal_command(
            script_path=script_path,
            title=title,
            executable=executable,
        )
    else:
        command = [*shlex.split(executable or "x-terminal-emulator")]
        if startup_script:
            command.extend(["-e", "sh", "-lc", startup_script])
    process = subprocess.Popen(command)
    pid_file.write_text(f"{process.pid}\n{shlex.join(command)}\n", encoding="utf-8")
    return pid_file


def launch_command_in_terminal(
    command: Sequence[str],
    *,
    title: str = "Arthexis Agent Terminal",
    state_key: str = "terminal",
    working_directory: Path | str | None = None,
    executable: str = "",
) -> Path:
    startup_script = _command_script(
        command,
        working_directory=working_directory,
        shell="powershell" if _is_windows() else "sh",
    )
    return _launch_startup_script(
        startup_script,
        executable=executable,
        title=title,
        state_key=state_key,
    )


def _launch_terminal(terminal: AgentTerminal) -> None:
    executable = terminal.resolved_executable()
    startup_script = _build_startup_script(terminal)
    if _is_windows():
        _launch_startup_script(
            startup_script,
            executable=executable,
            title=terminal.name or "Arthexis Agent Terminal",
            state_key=str(terminal.pk),
        )
        return
    pid_dir = _terminal_state_dir()
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _terminal_pid_file(terminal.pk)
    command = [*shlex.split(executable)]
    if startup_script:
        command.extend(["-e", "sh", "-lc", startup_script])
    process = subprocess.Popen(command)
    pid_file.write_text(f"{process.pid}\n{shlex.join(command)}\n", encoding="utf-8")


def _matches_current_node_role(terminal: AgentTerminal) -> bool:
    current_node_role = str(getattr(settings, "NODE_ROLE", "Terminal") or "Terminal").strip().lower()
    node_role = terminal.effective_node_role()
    target_node_role = str(getattr(node_role, "name", node_role) or "Terminal").strip().lower()
    return current_node_role == target_node_role


@shared_task(name="terminals.ensure_agent_terminals")
def ensure_agent_terminals() -> int:
    if not _has_desktop_ui():
        return 0
    launched = 0
    for terminal in AgentTerminal.assigned_to_any_user():
        if not _matches_current_node_role(terminal):
            continue
        pid_file = _terminal_pid_file(terminal.pk)
        if _terminal_running(pid_file):
            continue
        _launch_terminal(terminal)
        launched += 1
    return launched
