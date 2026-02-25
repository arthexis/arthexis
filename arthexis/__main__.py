"""CLI entrypoint for the Arthexis utility commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Return the repository root directory for this package checkout."""
    return Path(__file__).resolve().parents[1]


def _resolve_cli_path() -> Path:
    """Return the shell implementation path for the resolve subcommand."""
    return _repo_root() / "scripts" / "resolve_cli.sh"


def _run_resolve_subcommand(args: list[str]) -> int:
    """Execute the resolve shell entrypoint with passthrough arguments."""
    resolve_script = _resolve_cli_path()
    if not resolve_script.exists():
        raise FileNotFoundError(f"Resolve CLI helper not found: {resolve_script}")

    completed = subprocess.run([str(resolve_script), *args], check=False)
    return completed.returncode


def _print_usage() -> None:
    """Print high-level CLI usage text."""
    print("Usage: arthexis <subcommand> [args...]")
    print()
    print("Subcommands:")
    print("  resolve    Resolve sigils in text or files.")
    print()
    print("Use `arthexis resolve --help` for resolve options.")


def main(argv: list[str] | None = None) -> int:
    """Run the ``arthexis`` command line interface."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _print_usage()
        return 0

    subcommand, *sub_args = args
    if subcommand in {"-h", "--help", "help"}:
        _print_usage()
        return 0
    if subcommand == "resolve":
        return _run_resolve_subcommand(sub_args)

    print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
    _print_usage()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
