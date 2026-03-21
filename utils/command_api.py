"""Shared management command interface for shell and batch wrappers.

This module provides command discovery, deprecated-command filtering, validation,
and execution for both POSIX and Windows entrypoints.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TypedDict

from utils.python_env import resolve_project_python

ALLOWED_COMMAND_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DISCOVERED_COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_CACHE_TTL_SECONDS = 30
DEFAULT_MANAGE_TIMEOUT_SECONDS = 60
ABSORBED_COMMAND_DISCOVERY_SCRIPT = """
from django.core.management import get_commands
from importlib import import_module
for command_name, app_name in sorted(get_commands().items()):
    try:
        module = import_module(f"{app_name}.management.commands.{command_name}")
        cls = module.Command
    except Exception:
        continue
    if cls.__dict__.get("arthexis_absorbed_command", False):
        print(command_name)
""".strip()


class CommandApiError(RuntimeError):
    """Raised for canonical command API failures that should be user-visible."""


class LegacyInvocation(TypedDict):
    """Normalized pieces extracted from legacy ``command.sh`` arguments."""

    action: str
    option_flags: list[str]
    command: str | None
    command_args: list[str]


@dataclass(frozen=True)
class CommandOptions:
    """Runtime options shared by list/run operations."""

    celery: bool = False
    deprecated: bool = False

    @property
    def celery_flag(self) -> str:
        """Return the manage.py celery selector flag."""
        return "--celery" if self.celery else "--no-celery"


def _fast_run_enabled() -> bool:
    """Return whether run-mode should skip command discovery for faster execution."""
    return os.getenv("ARTHEXIS_COMMAND_FAST_RUN", "") in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def _cache_ttl_seconds() -> int:
    """Read and sanitize cache TTL from environment."""
    configured = os.getenv("ARTHEXIS_COMMAND_CACHE_TTL", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        value = int(configured)
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS
    return value if value > 0 else DEFAULT_CACHE_TTL_SECONDS


def _manage_timeout_seconds() -> int:
    """Read and sanitize manage.py timeout from environment."""
    configured = os.getenv(
        "ARTHEXIS_MANAGE_TIMEOUT", str(DEFAULT_MANAGE_TIMEOUT_SECONDS)
    )
    try:
        value = int(configured)
    except ValueError:
        return DEFAULT_MANAGE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_MANAGE_TIMEOUT_SECONDS


def _cache_file(base_dir: Path, name: str) -> Path:
    """Build a cache file path for command metadata."""
    return base_dir / ".cache" / name


def _read_cached_lines(cache_file: Path, ttl_seconds: int) -> list[str] | None:
    """Read cache file if fresh enough; otherwise return None."""
    try:
        if not cache_file.exists():
            return None
        cache_age = time.time() - cache_file.stat().st_mtime
        if cache_age >= ttl_seconds:
            return None
        return [
            line for line in cache_file.read_text(encoding="utf-8").splitlines() if line
        ]
    except OSError:
        return None


def _write_cache_lines(cache_file: Path, lines: Iterable[str]) -> None:
    """Persist cached lines atomically when possible."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_tmp = cache_file.with_suffix(f"{cache_file.suffix}.tmp")
        cache_tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cache_tmp.replace(cache_file)
    except OSError:
        return


def _run_manage(base_dir: Path, *args: str) -> str:
    """Run manage.py and return stdout.

    Raises:
        CommandApiError: When manage.py invocation fails.
    """
    cmd = [resolve_project_python(base_dir), "manage.py", *args]
    timeout = _manage_timeout_seconds()
    try:
        result = subprocess.run(
            cmd,
            cwd=base_dir,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandApiError(
            f"manage.py timed out after {timeout}s. Check Django configuration and dependencies."
        ) from exc
    if result.returncode != 0:
        raise CommandApiError(
            result.stderr.strip()
            or result.stdout.strip()
            or "manage.py invocation failed"
        )
    return result.stdout


def discover_commands(base_dir: Path, options: CommandOptions) -> list[str]:
    """Discover available Django command names for the selected celery mode."""
    ttl_seconds = _cache_ttl_seconds()
    cache_key = "celery" if options.celery else "no_celery"
    cache_file = _cache_file(base_dir, f"command_list_{cache_key}.txt")
    cached = _read_cached_lines(cache_file, ttl_seconds)
    if cached is not None:
        return cached

    output = _run_manage(base_dir, "help", "--commands", options.celery_flag)
    commands: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("["):
            continue
        for token in stripped.split():
            if DISCOVERED_COMMAND_RE.match(token):
                commands.append(token)

    if not commands:
        raise CommandApiError(
            "Command discovery returned no results. Check Django configuration."
        )

    deduped = sorted(set(commands))
    _write_cache_lines(cache_file, deduped)
    return deduped


def discover_absorbed_commands(base_dir: Path) -> set[str]:
    """Return command names marked as absorbed via ``arthexis_absorbed_command``."""
    ttl_seconds = _cache_ttl_seconds()
    cache_file = _cache_file(base_dir, "deprecated_absorbed_commands.txt")
    cached = _read_cached_lines(cache_file, ttl_seconds)
    if cached is not None:
        return set(cached)

    output = _run_manage(base_dir, "shell", "-c", ABSORBED_COMMAND_DISCOVERY_SCRIPT)
    absorbed = {line.strip() for line in output.splitlines() if line.strip()}
    _write_cache_lines(cache_file, sorted(absorbed))
    return absorbed


def filtered_commands(base_dir: Path, options: CommandOptions) -> list[str]:
    """Return discovered commands after applying deprecated filtering."""
    commands = discover_commands(base_dir, options)
    if options.deprecated:
        return commands
    absorbed = discover_absorbed_commands(base_dir)
    return [name for name in commands if name not in absorbed]


def normalize_command_name(raw_command: str) -> str:
    """Normalize and validate command names to Django's underscore style."""
    if not ALLOWED_COMMAND_RE.match(raw_command):
        raise ValueError(
            "Invalid command name. Command names may only contain letters, numbers, underscores, and hyphens."
        )
    return raw_command.replace("-", "_")


def _build_command_parser() -> argparse.ArgumentParser:
    """Create the parser for canonical command operations."""
    parser = argparse.ArgumentParser(prog="arthexis cmd", add_help=True)
    subparsers = parser.add_subparsers(dest="action")

    shared_parser = argparse.ArgumentParser(add_help=False)
    shared_parser.add_argument(
        "--deprecated", action="store_true", help="include absorbed/deprecated commands"
    )
    shared_group = shared_parser.add_mutually_exclusive_group()
    shared_group.add_argument(
        "--celery", action="store_true", help="discover commands in celery mode"
    )
    shared_group.add_argument(
        "--no-celery", action="store_true", help="discover commands outside celery mode"
    )

    subparsers.add_parser(
        "list", help="list available Django commands", parents=[shared_parser]
    )

    run_parser = subparsers.add_parser(
        "run", help="run a Django command", parents=[shared_parser]
    )
    run_parser.add_argument("django_command", help="Django command name")
    run_parser.add_argument("args", nargs=argparse.REMAINDER, help="command arguments")

    return parser


def _options_from_args(parsed: argparse.Namespace) -> CommandOptions:
    """Build command options from parsed arguments."""
    return CommandOptions(
        celery=bool(getattr(parsed, "celery", False)),
        deprecated=bool(parsed.deprecated),
    )


def _resolve_command(base_dir: Path, raw_command: str, options: CommandOptions) -> str:
    """Normalize and validate command existence against filtered command list."""
    try:
        command = normalize_command_name(raw_command)
    except ValueError as exc:
        raise CommandApiError(str(exc)) from exc

    commands = filtered_commands(base_dir, options)
    if command in commands:
        return command

    prefix_matches = [
        candidate for candidate in commands if candidate.startswith(command)
    ]
    contains_matches = [
        candidate
        for candidate in commands
        if command in candidate and not candidate.startswith(command)
    ]

    message_lines = [f"No exact match for '{raw_command}'."]
    if prefix_matches or contains_matches:
        message_lines.append("Possible commands:")
        message_lines.extend([f"  {match}" for match in prefix_matches])
        message_lines.extend([f"  {match}" for match in contains_matches])
    else:
        message_lines.append("Run 'arthexis cmd list' to see available commands.")
    raise CommandApiError("\n".join(message_lines))


def list_commands(base_dir: Path, options: CommandOptions) -> int:
    """Print available commands and usage hint."""
    commands = filtered_commands(base_dir, options)
    print("Available Django management commands:")
    for command in commands:
        print(command)
    print()
    print("Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]")
    print(
        "Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]"
    )
    return 0


def run_command(
    base_dir: Path,
    raw_command: str,
    command_args: Sequence[str],
    options: CommandOptions,
) -> int:
    """Validate and execute a Django command.

    Args:
        base_dir: Repository root containing ``manage.py``.
        raw_command: User-supplied command name before normalization.
        command_args: Additional arguments forwarded to the Django command.
        options: Discovery and celery mode options.

    Returns:
        The subprocess exit code returned by ``manage.py``.

    Raises:
        CommandApiError: When the command name is invalid or cannot be resolved.
    """
    if _fast_run_enabled():
        try:
            command = normalize_command_name(raw_command)
        except ValueError as exc:
            raise CommandApiError(str(exc)) from exc
    else:
        command = _resolve_command(base_dir, raw_command, options)

    process = subprocess.run(
        [
            resolve_project_python(base_dir),
            "manage.py",
            options.celery_flag,
            command,
            *command_args,
        ],
        cwd=base_dir,
        check=False,
    )
    return process.returncode


def _parse_legacy_invocation(argv: Sequence[str]) -> LegacyInvocation:
    """Split legacy wrapper arguments into canonical action components.

    Args:
        argv: Raw arguments passed to the legacy wrapper entrypoint.

    Returns:
        Structured legacy invocation details ready for canonical translation.
    """

    if not argv:
        return {
            "action": "list",
            "option_flags": [],
            "command": None,
            "command_args": [],
        }

    if argv[0] in {"list", "run"}:
        action = argv[0]
        command = argv[1] if action == "run" and len(argv) > 1 else None
        command_args = list(argv[2:]) if action == "run" and len(argv) > 2 else []
        return {
            "action": action,
            "option_flags": [],
            "command": command,
            "command_args": command_args,
        }

    option_flags: list[str] = []
    remaining = list(argv)
    while remaining and remaining[0] in {"--deprecated", "--celery", "--no-celery"}:
        option_flags.append(remaining.pop(0))

    command = remaining[0] if remaining else None
    command_args = remaining[1:] if len(remaining) > 1 else []
    return {
        "action": "run" if command else "list",
        "option_flags": option_flags,
        "command": command,
        "command_args": command_args,
    }


def parse_legacy_args(argv: list[str]) -> list[str]:
    """Translate legacy invocation syntax into canonical list/run actions."""
    invocation = _parse_legacy_invocation(argv)
    if invocation["action"] == "list":
        return ["list", *invocation["option_flags"]]
    if invocation["command"] is None:
        return ["list", *invocation["option_flags"]]
    return [
        "run",
        *invocation["option_flags"],
        invocation["command"],
        *invocation["command_args"],
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by shell and batch wrappers."""
    effective_argv = parse_legacy_args(list(argv if argv is not None else sys.argv[1:]))
    parser = _build_command_parser()
    parsed = parser.parse_args(effective_argv)
    base_dir = Path(__file__).resolve().parents[1]

    try:
        if parsed.action == "list":
            return list_commands(base_dir, _options_from_args(parsed))
        if parsed.action == "run":
            return run_command(
                base_dir, parsed.django_command, parsed.args, _options_from_args(parsed)
            )
    except CommandApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
