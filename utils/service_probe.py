"""Helpers for probing live Arthexis service reachability and runtime ports."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import http.client
import re
import shlex
import subprocess

RUNSERVER_PORT_PATTERN = re.compile(r":(\d{2,5})(?:\D|$)")
RUNSERVER_PORT_FLAG_PATTERN = re.compile(r"--port(?:=|\s+)(\d{2,5})", re.IGNORECASE)


@dataclass(frozen=True)
class ServiceProbeResult:
    """Container for HTTP probe outcomes."""

    reachable: bool
    status_code: int | None


def parse_runserver_port(command_line: str) -> int | None:
    """Extract a valid runserver port from *command_line* if one is present."""

    for pattern in (RUNSERVER_PORT_FLAG_PATTERN, RUNSERVER_PORT_PATTERN):
        match = pattern.search(command_line)
        if match:
            try:
                port = int(match.group(1))
            except ValueError:
                continue
            if 1 <= port <= 65535:
                return port

    try:
        tokens = shlex.split(command_line)
    except ValueError:
        tokens = command_line.split()

    try:
        runserver_index = tokens.index("runserver")
    except ValueError:
        return None

    options_with_values = {"--verbosity", "-v", "--settings", "--pythonpath", "--addrport"}
    skip_next = False
    for token in tokens[runserver_index + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if token in options_with_values:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if token.isdigit():
            port = int(token)
            return port if 1 <= port <= 65535 else None
        match = RUNSERVER_PORT_PATTERN.search(token)
        if match:
            port = int(match.group(1))
            return port if 1 <= port <= 65535 else None
        break

    return None


def detect_runserver_port() -> int | None:
    """Return the first discovered ``manage.py runserver`` port from process listings."""

    try:
        result = subprocess.run(
            ["pgrep", "-af", "manage.py runserver"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        port = parse_runserver_port(line)
        if port is not None:
            return port
    return None


def probe_admin_login(port: int, *, timeout: float = 1.0) -> ServiceProbeResult:
    """Probe ``/admin/login/`` on localhost and report whether HTTP responded successfully."""

    if not (1 <= int(port) <= 65535):
        return ServiceProbeResult(reachable=False, status_code=None)

    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request("GET", "/admin/login/")
        response = connection.getresponse()
        status_code = int(response.status)
        response.read()
        connection.close()
    except (OSError, http.client.HTTPException):
        return ServiceProbeResult(reachable=False, status_code=None)

    reachable = 200 <= status_code < 500
    return ServiceProbeResult(reachable=reachable, status_code=status_code)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser used for service probing commands.

    Returns:
        argparse.ArgumentParser: A parser configured with
            ``detect-runserver-port`` and ``probe-admin-login`` subcommands.
            The latter requires a ``--port`` argument.

    Raises:
        argparse.ArgumentError: If argparse encounters parser configuration
            conflicts while creating subcommands.
    """

    parser = argparse.ArgumentParser(description="Arthexis runtime service probing helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect-runserver-port")
    detect_parser.set_defaults(command="detect-runserver-port")

    probe_parser = subparsers.add_parser("probe-admin-login")
    probe_parser.add_argument("--port", type=int, required=True)
    probe_parser.set_defaults(command="probe-admin-login")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for shell scripts that need service probing utilities."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "detect-runserver-port":
        detected_port = detect_runserver_port()
        if detected_port is None:
            return 1
        print(detected_port)
        return 0

    if args.command == "probe-admin-login":
        result = probe_admin_login(args.port)
        return 0 if result.reachable else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
