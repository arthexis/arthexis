#!/usr/bin/env python3
"""Resolve sigils in text using the Django project configuration."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve sigils in text using the project's resolver."
    )
    parser.add_argument(
        "--text",
        help="Text containing sigils to resolve."
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Path to a file whose contents should be resolved."
    )
    parser.add_argument(
        "positional_text",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def _load_text(args: argparse.Namespace) -> str:
    positional_text = " ".join(arg for arg in args.positional_text if arg)

    provided = [value for value in (args.text, positional_text) if value]
    if args.file and provided:
        raise SystemExit("Cannot use --file together with text input.")
    if len(provided) > 1:
        raise SystemExit("Provide text either via --text or positionally, not both.")

    if args.file:
        if not args.file.exists():
            raise SystemExit(f"File not found: {args.file}")
        return args.file.read_text(encoding="utf-8")

    if provided:
        return provided[0]

    if sys.stdin.isatty():
        raise SystemExit(
            "No input provided. Use --text, --file, or pipe data into the command."
        )

    return sys.stdin.read()


def main() -> int:
    args = _parse_arguments()

    import django

    django.setup()

    from apps.sigils.sigil_resolver import resolve_sigils

    text = _load_text(args)
    resolved = resolve_sigils(text)
    sys.stdout.write(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
