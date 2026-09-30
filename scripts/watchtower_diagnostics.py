#!/usr/bin/env python3
"""Persist private Watchtower diagnostics and publish a sanitized journal view."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess


DEFAULT_RAW_DIR = Path("/var/lib/gway/deploy-diagnostics")
DEFAULT_SOURCE = "recipe/watchtower-deploy"
MAX_READ_BYTES = 1024 * 1024
MAX_JOURNAL_LINES = 400
MAX_RAW_SNAPSHOTS = 20

SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|proxy[-_]?authorization|api[-_]?key|access[-_]?key|"
    r"secret(?:[-_]?key)?|client[-_]?secret|token|password|passwd|cookie|"
    r"set-cookie|signature|credential)\b(\s*[:=]\s*|\s+)([^\s,;]+)"
)
SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|auth|authorization|credential|"
    r"key|password|passwd|secret|signature|sig|token|x-amz-signature)=)([^&#\s]+)"
)
BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
URL_USERINFO = re.compile(r"(https?://)([^/@\s:]+):([^/@\s]+)@")
KNOWN_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"
)
JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")


def sanitize_line(line: str) -> str:
    """Redact common secret forms while retaining useful diagnostic context."""
    if PRIVATE_KEY.search(line):
        return "[REDACTED PRIVATE KEY MATERIAL]"
    line = AUTH_HEADER.sub(r"\\1: [REDACTED]", line)
    line = URL_USERINFO.sub(r"\1[REDACTED]@", line)
    line = BEARER.sub("Bearer [REDACTED]", line)
    line = SENSITIVE_QUERY.sub(r"\1[REDACTED]", line)
    line = SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", line)
    line = KNOWN_TOKEN.sub("[REDACTED TOKEN]", line)
    line = JWT.sub("[REDACTED JWT]", line)
    return line


def _tail_text(path: Path) -> tuple[list[str], bool]:
    data = path.read_bytes()
    truncated_bytes = len(data) > MAX_READ_BYTES
    if truncated_bytes:
        data = data[-MAX_READ_BYTES:]
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    truncated_lines = len(lines) > MAX_JOURNAL_LINES
    if truncated_lines:
        lines = lines[-MAX_JOURNAL_LINES:]
    return lines, truncated_bytes or truncated_lines


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "unknown"


def persist_raw(
    source: Path,
    *,
    raw_dir: Path,
    run_id: str,
    attempt: str,
    step: str,
) -> Path:
    """Copy one raw diagnostic to a root-private bounded snapshot directory."""
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_dir.chmod(0o700)
    name = (
        f"watchtower-{_safe_component(run_id)}-"
        f"{_safe_component(attempt)}-{_safe_component(step)}.log"
    )
    destination = raw_dir / name
    shutil.copyfile(source, destination)
    destination.chmod(0o600)

    snapshots = sorted(
        raw_dir.glob("watchtower-*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in snapshots[MAX_RAW_SNAPSHOTS:]:
        stale.unlink(missing_ok=True)
    return destination


def publish_sanitized(
    source: Path,
    *,
    journal_source: str,
    run_id: str,
    attempt: str,
    step: str,
    raw_snapshot: Path,
) -> None:
    """Publish a redacted, bounded view under a Gway-queryable journal source."""
    lines, truncated = _tail_text(source)
    header = (
        "watchtower_diagnostic "
        f"run_id={run_id} attempt={attempt} step={step} "
        f"raw_snapshot={raw_snapshot.name} truncated={'true' if truncated else 'false'}"
    )
    rendered = [header]
    rendered.extend(
        f"[{step}] {sanitize_line(line)}"
        for line in lines
        if line.strip()
    )
    subprocess.run(
        [
            "systemd-cat",
            "--identifier",
            journal_source,
            "--priority",
            "err",
        ],
        input="\n".join(rendered) + "\n",
        text=True,
        check=True,
    )


def snapshot(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"diagnostic input does not exist: {source}")
    raw_snapshot = persist_raw(
        source,
        raw_dir=Path(args.raw_dir),
        run_id=args.run_id,
        attempt=args.attempt,
        step=args.step,
    )
    publish_sanitized(
        source,
        journal_source=args.source,
        run_id=args.run_id,
        attempt=args.attempt,
        step=args.step,
        raw_snapshot=raw_snapshot,
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--input", required=True)
    result.add_argument("--source", default=DEFAULT_SOURCE)
    result.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    result.add_argument("--run-id", required=True)
    result.add_argument("--attempt", required=True)
    result.add_argument("--step", required=True)
    return result


def main() -> int:
    return snapshot(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
