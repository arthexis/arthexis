from __future__ import annotations

import shlex
import subprocess

from celery import shared_task
from django.conf import settings

from .models import AgentTerminal


def _has_desktop_ui() -> bool:
    return bool(getattr(settings, "DESKTOP_UI_ENABLED", False) or getattr(settings, "DESKTOP_UI", False))


def _terminal_running(process_match: str) -> bool:
    if not process_match:
        return False
    result = subprocess.run(["pgrep", "-f", process_match], check=False, capture_output=True, text=True)
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _send_lines(terminal: AgentTerminal, lines: list[str]) -> None:
    for line in lines:
        if not line.strip():
            continue
        subprocess.run(["xdotool", "type", "--delay", "1", line], check=False)
        subprocess.run(["xdotool", "key", "Return"], check=False)


def _launch_terminal(terminal: AgentTerminal) -> None:
    executable = terminal.resolved_executable()
    command = [*shlex.split(executable)]
    subprocess.Popen(command)
    command_lines = [terminal.resolved_launch_command(), terminal.resolved_launch_prompt()]
    blocks = terminal.resolved_prompt_blocks()
    if terminal.prompt_block_mode == AgentTerminal.LOOP_REPEAT and blocks:
        blocks = blocks + blocks
    _send_lines(terminal, [*command_lines, *blocks])


@shared_task(name="terminals.ensure_agent_terminals")
def ensure_agent_terminals() -> int:
    if not _has_desktop_ui():
        return 0
    launched = 0
    for terminal in AgentTerminal.assigned_to_any_user():
        process_match = terminal.resolved_executable()
        if _terminal_running(process_match):
            continue
        _launch_terminal(terminal)
        launched += 1
    return launched
