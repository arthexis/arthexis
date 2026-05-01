from __future__ import annotations

import os
import shlex
from pathlib import Path
import subprocess

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


def _launch_terminal(terminal: AgentTerminal) -> None:
    pid_dir = _terminal_state_dir()
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _terminal_pid_file(terminal.pk)
    executable = terminal.resolved_executable()
    startup_script = _build_startup_script(terminal)
    if _is_windows():
        raise RuntimeError(
            "_launch_terminal does not support Windows POSIX shell launch "
            f"for terminal pk={terminal.pk!r}; startup-script-present={bool(startup_script)!r}"
        )
    command = [*shlex.split(executable)]
    if startup_script:
        command.extend(["-e", "sh", "-lc", startup_script])
    process = subprocess.Popen(command)
    pid_file.write_text(f"{process.pid}\n{' '.join(command)}\n")


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
