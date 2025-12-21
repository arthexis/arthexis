"""Generate a badge showing reachable Watchtower nodes."""
from __future__ import annotations

import argparse
import http.client
import json
import ssl
from pathlib import Path
from typing import Iterable

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.coverage import render_badge

DEFAULT_BADGE_PATH = Path("media/watchtowers.svg")
DEFAULT_TIMEOUT = 5
DEFAULT_LABEL = "Watchtowers"
BADGE_GREEN = "#28a745"
BADGE_BLUE = "#007ec6"
BADGE_RED = "#e05d44"


class WatchtowerTarget:
    """Representation of a Watchtower endpoint discovered from fixtures."""

    __slots__ = ("hostname", "port", "source")

    def __init__(self, hostname: str, port: int, source: Path) -> None:
        self.hostname = hostname
        self.port = port
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"WatchtowerTarget(hostname={self.hostname!r}, port={self.port}, source={self.source})"


def iter_watchtower_fixtures(fixtures_dir: Path) -> Iterable[Path]:
    """Yield fixture files that describe Watchtower nodes."""

    for path in sorted(fixtures_dir.glob("nodes__node_watchtower_*.json")):
        if path.is_file():
            yield path


def load_watchtower_targets(fixtures_dir: Path) -> list[WatchtowerTarget]:
    """Extract Watchtower endpoints from fixture files."""

    targets: list[WatchtowerTarget] = []
    for fixture in iter_watchtower_fixtures(fixtures_dir):
        try:
            payload = json.loads(fixture.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in payload if isinstance(payload, list) else []:
            if not isinstance(entry, dict):
                continue
            fields = entry.get("fields") or {}
            role = fields.get("role")
            if isinstance(role, (list, tuple)):
                if "Watchtower" not in role:
                    continue
            elif role != "Watchtower":
                continue
            hostname = str(fields.get("hostname") or "").strip()
            if not hostname:
                continue
            port_value = fields.get("port")
            try:
                port = int(port_value)
            except (TypeError, ValueError):
                port = 443
            targets.append(WatchtowerTarget(hostname, port, fixture))
    return targets


def is_online(target: WatchtowerTarget, *, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Return True if the HTTPS endpoint responds without certificate validation."""

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        connection = http.client.HTTPSConnection(
            target.hostname, target.port, timeout=timeout, context=context
        )
        connection.request("HEAD", "/")
        response = connection.getresponse()
        response.read()
        return bool(response.status)
    except OSError:
        return False
    finally:
        try:
            connection.close()
        except Exception:
            pass


def write_badge(count: int, output_path: Path) -> None:
    """Render the watchtower badge to ``output_path``."""

    if count >= 2:
        color = BADGE_GREEN
    elif count == 1:
        color = BADGE_BLUE
    else:
        color = BADGE_RED
    svg = render_badge(DEFAULT_LABEL, str(count), color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "apps" / "nodes" / "fixtures",
        help="Directory containing Watchtower node fixtures.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_BADGE_PATH,
        help="Path to write the generated badge SVG.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTPS connection timeout for reachability checks.",
    )
    parser.add_argument(
        "--assume-online",
        action="store_true",
        help=(
            "Count all Watchtower fixtures as online without network checks. "
            "Intended for non-interactive runs only."
        ),
    )
    args = parser.parse_args()
    if args.assume_online and sys.stdin.isatty():
        parser.error("--assume-online is only allowed for non-interactive runs.")

    targets = load_watchtower_targets(args.fixtures_dir)
    if not targets:
        write_badge(0, args.output)
        return 0

    if args.assume_online:
        online = len(targets)
    else:
        online = 0
        for target in targets:
            if is_online(target, timeout=args.timeout):
                online += 1
    write_badge(online, args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
