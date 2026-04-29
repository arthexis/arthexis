from __future__ import annotations

import shlex
from pathlib import Path
import subprocess

from celery import shared_task
from django.conf import settings

from .models import AgentTerminal


def _has_desktop_ui() -> bool:
    return bool(getattr(settings, "DESKTOP_UI_ENABLED", False) or getattr(settings, "DESKTOP_UI", False))


def _terminal_running(process_match: str) -> bool:
    pid_file = Path(process_match)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        if pid <= 0:
            return False
        subprocess.run(["kill", "-0", str(pid)], check=True, capture_output=True)
        return True
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def _build_startup_script(terminal: AgentTerminal) -> str:
    lines = [terminal.resolved_launch_command(), terminal.resolved_launch_prompt()]
    blocks = terminal.resolved_prompt_blocks()
    if terminal.prompt_block_mode == AgentTerminal.LOOP_REPEAT and blocks:
        blocks = [*blocks]
    lines.extend(blocks)
    script_lines: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        script_lines.append(text)
    return "\n".join(script_lines)


def _launch_terminal(terminal: AgentTerminal) -> None:
    pid_dir = Path("/tmp/arthexis-agent-terminals")
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / f"{terminal.pk}.pid"
    executable = terminal.resolved_executable()
    startup_script = _build_startup_script(terminal)
    command = [*shlex.split(executable)]
    if startup_script:
        command.extend(["-e", "sh", "-lc", startup_script])
    process = subprocess.Popen(command)
    pid_file.write_text(str(process.pid))


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
        process_match = f"/tmp/arthexis-agent-terminals/{terminal.pk}.pid"
        if _terminal_running(process_match):
            continue
        _launch_terminal(terminal)
        launched += 1
    return launched
