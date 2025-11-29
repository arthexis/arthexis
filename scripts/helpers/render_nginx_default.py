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
    default_reject_server,
    http_redirect_server,
    http_proxy_server,
    https_proxy_server,
    write_if_changed,
)

HTTP_IPV4_LISTENS = (
    "0.0.0.0:80",
    "0.0.0.0:8000",
    "0.0.0.0:8080",
    "0.0.0.0:8900",
)

HTTP_IPV6_LISTENS = (
    "[::]:80",
    "[::]:8000",
    "[::]:8080",
    "[::]:8900",
)

HTTPS_IPV4_LISTENS = ("443 ssl",)

HTTPS_IPV6_LISTENS = ("[::]:443 ssl",)


def generate_config(
    mode: str,
    port: int,
    *,
    http_server_names: str | None = None,
    https_server_names: str | None = None,
    include_ipv6: bool = False,
) -> str:
    mode = mode.lower()
    if mode not in {"internal", "public"}:
        raise ValueError(f"Unsupported mode: {mode}")

    http_listens = list(HTTP_IPV4_LISTENS)
    if include_ipv6:
        http_listens.extend(HTTP_IPV6_LISTENS)

    https_listens = list(HTTPS_IPV4_LISTENS)
    if include_ipv6:
        https_listens.extend(HTTPS_IPV6_LISTENS)

    if mode == "public":
        http_names = http_server_names or "arthexis.com *.arthexis.com"
        https_names = https_server_names or "arthexis.com *.arthexis.com"
        http_block = http_redirect_server(
            http_names,
            listens=http_listens,
        )
        https_block = https_proxy_server(
            https_names,
            port,
            listens=https_listens,
            trailing_slash=False,
        )
        http_default = default_reject_server(http_listens)
        https_default = default_reject_server(https_listens, https=True)
        return f"{http_block}\n\n{http_default}\n\n{https_block}\n\n{https_default}\n"

    http_names = http_server_names or "_"
    http_block = http_proxy_server(
        http_names,
        port,
        http_listens,
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
    parser.add_argument(
        "--ip6",
        action="store_true",
        help="Include IPv6 listeners in the rendered configuration",
    )

    args = parser.parse_args(argv)

    content = generate_config(
        args.mode,
        args.port,
        http_server_names=args.http_server_names,
        https_server_names=args.https_server_names,
        include_ipv6=args.ip6,
    )

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    changed = write_if_changed(args.dest, content)

    if changed:
        print(f"Wrote nginx config to {args.dest}")
    else:
        print(f"nginx config at {args.dest} unchanged")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
