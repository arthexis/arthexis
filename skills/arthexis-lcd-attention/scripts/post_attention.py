#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import unicodedata
from datetime import datetime, timedelta, timezone
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


def _render_lcd_lock_file(
    *, subject: str, body: str, expires_at: datetime | None = None
) -> str:
    lines = [subject.strip()[:64], body.strip()[:64]]
    if expires_at is not None:
        lines.append(expires_at.isoformat())
    return "\n".join(lines) + "\n"


def _write_message(
    base_dir: Path, subject: str, body: str, expires_at: datetime | None = None
) -> subprocess.CompletedProcess[str]:
    if not base_dir.exists():
        raise FileNotFoundError(f"Arthexis base directory not found at {base_dir}")

    lock_dir = base_dir / ".locks"
    lock_file = lock_dir / "lcd-high"
    lock_dir.mkdir(parents=True, exist_ok=True)
    payload = _render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)
    temp_file = lock_file.with_name(f".{lock_file.name}.{os.getpid()}.tmp")
    temp_file.write_text(payload, encoding="utf-8")
    temp_file.replace(lock_file)
    return subprocess.CompletedProcess(
        args=["write-lcd-lock", str(lock_file)],
        returncode=0,
        stdout=f"Updated {lock_file}\n",
        stderr="",
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

    if duration_seconds > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        return _write_message(base_dir, subject, body, expires_at=expires_at)
    return _write_message(base_dir, subject, body)


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
        help=(
            "Keep the sticky LCD message visible with a bounded expiry "
            f"for {int(DEFAULT_SUSTAIN_SECONDS)} seconds."
        ),
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
        help=(
            "Accepted for compatibility; sustained mode uses a bounded lock expiry "
            f"instead of a refresh loop (default: {int(DEFAULT_REFRESH_INTERVAL)})."
        ),
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
        print("sustain_mode: bounded lock expiry")

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
