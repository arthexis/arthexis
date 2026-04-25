#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

WIDTH = 16
DEFAULT_BASE_DIR = Path("/home/arthe/arthexis")
DEFAULT_CODE_WIDTH = 10
DEFAULT_SUSTAIN_SECONDS = 600.0
DEFAULT_REFRESH_INTERVAL = 15.0


def _ascii_upper(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch if ch.isalnum() or ch in {" ", "-", "_", ":"} else " " for ch in ascii_text)
    return " ".join(cleaned.upper().split())


def _display_code(raw_code: str) -> str:
    code = _ascii_upper(raw_code)
    if not code:
        raise ValueError("code must contain at least one ASCII letter or digit")
    return code[:DEFAULT_CODE_WIDTH].rstrip()


def _display_time(raw_time: str | None) -> str:
    if raw_time:
        candidate = raw_time.strip()
        datetime.strptime(candidate, "%H:%M")
        return candidate
    return datetime.now().astimezone().strftime("%H:%M")


def _display_hint(hint: str, action: str | None) -> str:
    words: list[str] = []
    for value in (hint, action or ""):
        words.extend(_ascii_upper(value).split())
    if not words:
        raise ValueError("hint must contain at least one ASCII word")
    return " ".join(words[:2])[:WIDTH].rstrip()


def build_lines(code: str, hint: str, action: str | None, time_text: str | None) -> tuple[str, str]:
    display_code = _display_code(code)
    display_time = _display_time(time_text)
    subject = f"{display_code:<{DEFAULT_CODE_WIDTH}} {display_time:>5}"[:WIDTH]
    body = _display_hint(hint, action)
    return subject, body


def _suite_python(base_dir: Path) -> str:
    venv_python = base_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    python3 = shutil.which("python3")
    if python3:
        return python3
    return sys.executable


def _write_message(base_dir: Path, subject: str, body: str) -> subprocess.CompletedProcess[str]:
    manage_py = base_dir / "manage.py"
    if not manage_py.exists():
        raise FileNotFoundError(f"Arthexis manage.py not found at {manage_py}")

    command = [
        _suite_python(base_dir),
        str(manage_py),
        "lcd",
        "write",
        "--subject",
        subject,
        "--body",
        body,
        "--sticky",
    ]
    return subprocess.run(
        command,
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_sustain_seconds(
    *,
    sustained: bool,
    sustain_seconds: float | None,
    max_sustain_seconds: float,
) -> float:
    if max_sustain_seconds <= 0:
        raise ValueError("max sustain seconds must be greater than zero")
    if sustain_seconds is None:
        duration = DEFAULT_SUSTAIN_SECONDS if sustained else 0.0
    else:
        duration = sustain_seconds
    if duration < 0:
        raise ValueError("sustain seconds must be zero or greater")
    if duration > max_sustain_seconds:
        raise ValueError("sustain seconds exceeds the configured maximum")
    return duration


def _write_sustained_message(
    *,
    base_dir: Path,
    subject: str,
    body: str,
    duration_seconds: float,
    interval_seconds: float,
) -> subprocess.CompletedProcess[str]:
    if interval_seconds <= 0:
        raise ValueError("interval seconds must be greater than zero")

    result = _write_message(base_dir, subject, body)
    if result.returncode != 0 or duration_seconds <= 0:
        return result

    deadline = time.monotonic() + duration_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        time.sleep(min(interval_seconds, remaining))
        result = _write_message(base_dir, subject, body)
        if result.returncode != 0:
            return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render and optionally send a short Arthexis LCD attention message."
    )
    parser.add_argument("--code", required=True, help="Short code title for line 1.")
    parser.add_argument("--hint", required=True, help="Primary hint word for line 2.")
    parser.add_argument("--action", help="Optional second hint word for line 2.")
    parser.add_argument("--time", dest="time_text", help="Override local message time as HH:MM.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE_DIR,
        help=f"Arthexis suite base directory (default: {DEFAULT_BASE_DIR})",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Render the lines but do not write to the LCD.",
    )
    parser.add_argument(
        "--critical",
        action="store_true",
        help="Mark this as a critical high-priority attention request.",
    )
    parser.add_argument(
        "--sustained",
        action="store_true",
        help=f"Refresh the sticky LCD message for {int(DEFAULT_SUSTAIN_SECONDS)} seconds.",
    )
    parser.add_argument(
        "--sustain-seconds",
        type=float,
        help="Bounded duration for sustained refreshes; use 0 for a single sticky write.",
    )
    parser.add_argument(
        "--max-sustain-seconds",
        type=float,
        default=DEFAULT_SUSTAIN_SECONDS,
        help=f"Maximum allowed sustain duration (default: {int(DEFAULT_SUSTAIN_SECONDS)}).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_REFRESH_INTERVAL,
        help=f"Seconds between sustained refreshes (default: {int(DEFAULT_REFRESH_INTERVAL)}).",
    )
    args = parser.parse_args()

    try:
        subject, body = build_lines(args.code, args.hint, args.action, args.time_text)
        sustain_seconds = _resolve_sustain_seconds(
            sustained=args.sustained,
            sustain_seconds=args.sustain_seconds,
            max_sustain_seconds=args.max_sustain_seconds,
        )
        if args.interval <= 0:
            raise ValueError("interval seconds must be greater than zero")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"line1: {subject}")
    print(f"line2: {body}")
    if args.critical:
        print("priority: critical")
    if sustain_seconds > 0:
        print(f"sustain_seconds: {sustain_seconds:g}")
        print(f"refresh_interval: {args.interval:g}")

    if args.print_only:
        return 0

    try:
        result = _write_sustained_message(
            base_dir=args.base_dir,
            subject=subject,
            body=body,
            duration_seconds=sustain_seconds,
            interval_seconds=args.interval,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.returncode != 0:
        error_output = (result.stderr or result.stdout or "unknown error").strip()
        print(f"error: {error_output}", file=sys.stderr)
        return result.returncode or 1

    if result.stdout.strip():
        print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
