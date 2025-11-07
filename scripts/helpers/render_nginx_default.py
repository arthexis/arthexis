#!/usr/bin/env python3
"""Render the primary nginx configuration used by install.sh."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.helpers.nginx_config import (
    http_proxy_server,
    https_proxy_server,
    write_if_changed,
)

HTTP_LISTENS = (
    "0.0.0.0:80",
    "[::]:80",
    "0.0.0.0:8000",
    "[::]:8000",
    "0.0.0.0:8080",
    "[::]:8080",
)


def generate_config(
    mode: str,
    port: int,
    *,
    http_server_names: str | None = None,
    https_server_names: str | None = None,
) -> str:
    mode = mode.lower()
    if mode not in {"internal", "public"}:
        raise ValueError(f"Unsupported mode: {mode}")

    if mode == "public":
        http_names = http_server_names or "arthexis.com *.arthexis.com _"
        https_names = https_server_names or "arthexis.com *.arthexis.com"
        http_block = http_proxy_server(
            http_names,
            port,
            HTTP_LISTENS,
            trailing_slash=False,
        )
        https_block = https_proxy_server(
            https_names,
            port,
            trailing_slash=False,
        )
        return f"{http_block}\n\n{https_block}\n"

    http_names = http_server_names or "_"
    http_block = http_proxy_server(
        http_names,
        port,
        HTTP_LISTENS,
        trailing_slash=False,
    )
    return f"{http_block}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, help="nginx mode (internal or public)")
    parser.add_argument("--port", type=int, required=True, help="Application port proxied by nginx")
    parser.add_argument("--dest", type=Path, required=True, help="Destination nginx config path")
    parser.add_argument(
        "--http-server-names",
        default=None,
        help="Override server_name values for the HTTP block",
    )
    parser.add_argument(
        "--https-server-names",
        default=None,
        help="Override server_name values for the HTTPS block",
    )

    args = parser.parse_args(argv)

    content = generate_config(
        args.mode,
        args.port,
        http_server_names=args.http_server_names,
        https_server_names=args.https_server_names,
    )

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    changed = write_if_changed(args.dest, content)

    if changed:
        print(f"Wrote nginx config to {args.dest}")
        return 2

    print(f"nginx config at {args.dest} unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
