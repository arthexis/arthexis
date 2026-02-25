"""CLI entrypoint for the Arthexis utility commands."""

from __future__ import annotations

import importlib.resources
import subprocess
import sys


def _run_resolve_subcommand(args: list[str]) -> int:
    """Execute the resolve shell entrypoint with passthrough arguments."""
    script_resource = importlib.resources.files("arthexis.scripts").joinpath("resolve_cli.sh")
    with importlib.resources.as_file(script_resource) as resolve_script_path:
        completed = subprocess.run(["bash", str(resolve_script_path), *args], check=False)
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
