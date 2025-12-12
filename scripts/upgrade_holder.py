#!/usr/bin/env python3
"""Serve a temporary upgrade holder page on the backend port.

This script is intended to run as a transient systemd service during
auto-upgrade downtime when nginx is configured for the node. It renders a
simple status page that instructs users to wait for the upgrade to finish and
refreshes the browser periodically until the main service is available again.
"""

from __future__ import annotations

import argparse
import http.server
import signal
import socketserver
import sys
from dataclasses import dataclass
from html import escape


@dataclass
class HolderConfig:
    port: int
    message: str
    refresh_seconds: int


class UpgradeHolderRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve a short HTML page to notify users of the upgrade."""

    server_version = "ArthexisUpgradeHolder/1.0"
    sys_version = ""

    def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._write_response(b"")

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        content = self.server.render_page().encode("utf-8")  # type: ignore[attr-defined]
        self._write_response(content)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - inherited name
        # Silence default logging to keep systemd journal tidy
        return

    def _write_response(self, content: bytes) -> None:
        # Use 200 to avoid nginx error interception replacing the holder page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Retry-After", "30")
        self.end_headers()
        if content:
            self.wfile.write(content)


class UpgradeHolderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_cls: type[UpgradeHolderRequestHandler], config: HolderConfig):
        super().__init__(server_address, handler_cls)
        self.config = config

    def render_page(self) -> str:
        escaped_message = escape(self.config.message)
        refresh_ms = max(self.config.refresh_seconds, 1) * 1000
        return f"""
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Arthexis upgrade in progress</title>
    <meta http-equiv=\"refresh\" content=\"{self.config.refresh_seconds}\" />
    <style>
      body {{
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        background: #0f172a;
        color: #e2e8f0;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        margin: 0;
        padding: 2rem;
      }}
      .card {{
        max-width: 640px;
        background: rgba(15, 23, 42, 0.75);
        border: 1px solid #1e293b;
        border-radius: 16px;
        padding: 2rem;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
      }}
      h1 {{
        margin: 0 0 0.75rem;
        font-size: 1.75rem;
      }}
      p {{
        margin: 0 0 0.5rem;
        line-height: 1.6;
      }}
      .spinner {{
        margin: 1.5rem 0;
        width: 48px;
        height: 48px;
        border-radius: 50%;
        border: 4px solid #1e293b;
        border-top-color: #38bdf8;
        animation: spin 1s linear infinite;
      }}
      @keyframes spin {{
        to {{
          transform: rotate(360deg);
        }}
      }}
    </style>
    <script>
      const refreshInterval = {refresh_ms};
      async function checkReady() {{
        try {{
          await fetch(window.location.href, {{ cache: 'no-store', mode: 'no-cors' }});
          window.location.reload();
        }} catch (error) {{
          // Ignore failures and keep waiting
        }}
      }}
      setInterval(checkReady, refreshInterval);
    </script>
  </head>
  <body>
    <div class=\"card\">
      <h1>Upgrade in progress</h1>
      <div class=\"spinner\"></div>
      <p>{escaped_message}</p>
      <p>This page will refresh automatically when Arthexis is back online.</p>
    </div>
  </body>
</html>
"""


def _parse_args(argv: list[str]) -> HolderConfig:
    parser = argparse.ArgumentParser(description="Run the Arthexis upgrade holder server")
    parser.add_argument("--port", type=int, required=True, help="Port to bind the holder server to")
    parser.add_argument(
        "--message",
        type=str,
        default="An upgrade is running. Please keep this tab open while we refresh the service.",
        help="Message displayed to users while the upgrade completes",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=5,
        help="How often the page should refresh itself (seconds)",
    )

    args = parser.parse_args(argv)

    if args.port < 1 or args.port > 65535:
        raise SystemExit("Port must be between 1 and 65535")

    refresh_seconds = max(args.refresh_seconds, 1)

    return HolderConfig(port=args.port, message=args.message.strip(), refresh_seconds=refresh_seconds)


def _serve(config: HolderConfig) -> None:
    with UpgradeHolderServer(("0.0.0.0", config.port), UpgradeHolderRequestHandler, config) as httpd:
        def _shutdown(*_: object) -> None:
            httpd.shutdown()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> None:
    config = _parse_args(argv or sys.argv[1:])
    try:
        _serve(config)
    except OSError as exc:
        sys.stderr.write(f"Failed to start upgrade holder on port {config.port}: {exc}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
