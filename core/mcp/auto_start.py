"""Auto-start helpers for the MCP sigil resolver server."""

from __future__ import annotations

import logging
import os
import sys
import threading

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

from . import process as mcp_process

__all__ = ["schedule_auto_start"]

logger = logging.getLogger(__name__)

DEFAULT_DELAY_SECONDS = 2.0

# Management commands that should never trigger the MCP auto-start workflow.
_SKIP_MANAGEMENT_COMMANDS: set[str] = {
    "changepassword",
    "check",
    "collectstatic",
    "compilemessages",
    "createsuperuser",
    "dbshell",
    "dumpdata",
    "flush",
    "loaddata",
    "makemessages",
    "makemigrations",
    "migrate",
    "shell",
    "shell_plus",
    "sqlflush",
    "test",
}


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _management_command_name(argv: list[str]) -> str | None:
    if not argv:
        return None
    if argv[0].endswith("manage.py") and len(argv) > 1:
        return argv[1]
    return None


def _should_schedule_auto_start() -> bool:
    """Return ``True`` when the current process should schedule auto-start."""

    if _is_truthy(os.environ.get("MCP_AUTO_START_DISABLED")):
        return False

    # pytest sets this environment variable for each running test. When the
    # value is truthy we avoid mutating the test process state by starting
    # background timers or subprocesses.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False

    argv = list(sys.argv or [])
    management_command = _management_command_name(argv)
    if management_command is not None:
        if management_command == "runserver":
            if "--noreload" in argv[2:]:
                return True
            return os.environ.get("RUN_MAIN") == "true"
        if management_command in _SKIP_MANAGEMENT_COMMANDS:
            return False
        # Assume all other management commands run in a foreground process
        # where starting background services is acceptable.
        return True

    if not argv:
        return True

    command = argv[0].lower()
    if "pytest" in command or "py.test" in command:
        return False
    if command.endswith("celery") or command.startswith("celery"):
        return False
    if "celery" in command:
        return False

    return True


def _has_active_assistant_profile() -> bool:
    """Return ``True`` when at least one active Assistant Profile exists."""

    try:
        connection = connections[DEFAULT_DB_ALIAS]
    except Exception:  # pragma: no cover - defensive fallback
        return False

    if not connection.settings_dict:
        return False

    try:
        if not connection.is_usable():  # pragma: no cover - best effort
            return False
    except Exception:
        # Some database backends do not implement ``is_usable``. Continue and
        # rely on the query to raise an OperationalError when unavailable.
        pass

    try:
        from core.models import AssistantProfile

        return AssistantProfile.objects.filter(is_active=True).exists()
    except (OperationalError, ProgrammingError, DatabaseError):
        return False


def _resolve_delay_seconds(delay: float | None = None) -> float:
    if delay is not None:
        try:
            return max(float(delay), 0.0)
        except (TypeError, ValueError):
            return DEFAULT_DELAY_SECONDS

    config = dict(getattr(settings, "MCP_SIGIL_SERVER", {}))
    configured_delay = config.get("auto_start_delay")
    if configured_delay is not None:
        try:
            return max(float(configured_delay), 0.0)
        except (TypeError, ValueError):
            pass
    return DEFAULT_DELAY_SECONDS


def _start_server_if_needed() -> bool:
    """Start the MCP server when required.

    Returns ``True`` when a start was attempted, ``False`` otherwise.
    """

    if not _has_active_assistant_profile():
        return False

    try:
        status = mcp_process.get_status()
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Unable to determine MCP server status for auto-start")
        return False

    if status.get("running"):
        return False

    try:
        pid = mcp_process.start_server()
    except mcp_process.ServerAlreadyRunningError:
        return False
    except mcp_process.ServerStartError:
        logger.exception("Unable to auto-start MCP server")
        return False
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Unexpected error auto-starting MCP server")
        return False

    logger.info("Auto-started MCP server because an active Assistant Profile is present (PID %s).", pid)
    return True


def schedule_auto_start(
    *, delay: float | None = None, check_profiles_immediately: bool = True
) -> bool:
    """Schedule the MCP server auto-start if the environment requires it.

    When ``check_profiles_immediately`` is ``True`` (the default), the function
    verifies that an active Assistant Profile exists before scheduling the
    timer. When ``False``, the initial database lookup is deferred to the
    background callback so callers can avoid database access during
    initialization.

    Returns ``True`` when the timer has been scheduled, ``False`` otherwise.
    """

    if not _should_schedule_auto_start():
        return False

    if check_profiles_immediately and not _has_active_assistant_profile():
        return False

    delay_seconds = _resolve_delay_seconds(delay)

    timer = threading.Timer(delay_seconds, _start_server_if_needed)
    timer.daemon = True
    timer.start()
    return True
