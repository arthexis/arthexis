from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.meta.services import (
    DEFAULT_WHATSAPP_WEB_BROWSER,
    DEFAULT_WHATSAPP_WEB_CHANNEL,
    DEFAULT_WHATSAPP_WEB_PROFILE_DIR,
    dataclass_payload,
    parse_cli_date,
    read_whatsapp_web_messages,
    send_whatsapp_web_message,
    validate_whatsapp_web_login,
)


class Command(BaseCommand):
    help = "On-demand WhatsApp Web login, send, and read commands."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        login = subparsers.add_parser(
            "login",
            help="Open WhatsApp Web and wait for QR/profile login registration.",
        )
        self._add_browser_arguments(login, default_timeout=300.0)
        login.add_argument("--json", action="store_true", help="Emit JSON output.")

        status = subparsers.add_parser(
            "status",
            help="Check whether the persistent WhatsApp Web profile is logged in.",
        )
        self._add_browser_arguments(status, default_timeout=30.0)
        status.add_argument("--json", action="store_true", help="Emit JSON output.")

        send = subparsers.add_parser(
            "send",
            help="Send one WhatsApp Web message to a phone number.",
        )
        self._add_browser_arguments(send, default_timeout=120.0)
        send.add_argument("--to", required=True, help="Recipient phone number.")
        send.add_argument("--message", required=True, help="Message body to send.")
        send.add_argument(
            "--country-code",
            default="52",
            help="Country code for 10-digit local numbers. Default: 52.",
        )
        send.add_argument("--json", action="store_true", help="Emit JSON output.")

        read = subparsers.add_parser(
            "read",
            help="Read visible WhatsApp Web messages from a phone-number chat.",
        )
        self._add_browser_arguments(read, default_timeout=120.0)
        read.add_argument("--from", dest="from_phone", required=True, help="Phone number.")
        read.add_argument(
            "--country-code",
            default="52",
            help="Country code for 10-digit local numbers. Default: 52.",
        )
        read.add_argument("--date", help="Read messages from one YYYY-MM-DD date.")
        read.add_argument("--since", help="Read messages on or after YYYY-MM-DD.")
        read.add_argument("--until", help="Read messages on or before YYYY-MM-DD.")
        read.add_argument(
            "--new",
            action="store_true",
            help="Return messages after the local cursor for this phone/profile.",
        )
        read.add_argument(
            "--no-update-cursor",
            action="store_true",
            help="Do not advance the local --new cursor after reading.",
        )
        read.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum visible messages to return after filtering; 0 means all.",
        )
        read.add_argument("--json", action="store_true", help="Emit JSON output.")

    def _add_browser_arguments(self, parser, *, default_timeout: float) -> None:
        parser.add_argument(
            "--profile-dir",
            default=str(DEFAULT_WHATSAPP_WEB_PROFILE_DIR),
            help=(
                "Persistent browser profile directory. "
                f"Default: {DEFAULT_WHATSAPP_WEB_PROFILE_DIR}"
            ),
        )
        parser.add_argument(
            "--browser",
            default=DEFAULT_WHATSAPP_WEB_BROWSER,
            choices=("edge", "firefox", "chromium"),
            help=(
                "Browser engine to use. Defaults to Edge on Windows and Firefox "
                "elsewhere."
            ),
        )
        parser.add_argument(
            "--channel",
            default=DEFAULT_WHATSAPP_WEB_CHANNEL,
            help="Optional Playwright Chromium channel, for example msedge.",
        )
        parser.add_argument(
            "--cdp-url",
            default="",
            help="Attach to an already-open Chromium/Edge debugging URL.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=default_timeout,
            help="Seconds to wait for WhatsApp Web UI state.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds between UI-state checks.",
        )
        parser.add_argument(
            "--headless",
            action="store_true",
            help="Run headless. Headed mode is required for first QR login.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "login":
                result = self._handle_login(options)
            elif action == "status":
                result = self._handle_status(options)
            elif action == "send":
                result = self._handle_send(options)
            elif action == "read":
                result = self._handle_read(options)
            else:
                raise CommandError(f"Unknown whatsapp action: {action}")
        except (RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        if options.get("json"):
            self.stdout.write(json.dumps(dataclass_payload(result), indent=2))
            return None
        self._write_text_result(result)
        return None

    def _browser_options(self, options) -> dict[str, object]:
        return {
            "profile_dir": Path(options["profile_dir"]),
            "timeout_seconds": options["timeout"],
            "poll_interval_seconds": options["poll_interval"],
            "headless": options["headless"],
            "browser": options["browser"],
            "channel": options["channel"].strip(),
            "cdp_url": options["cdp_url"].strip(),
        }

    def _handle_login(self, options):
        return validate_whatsapp_web_login(**self._browser_options(options))

    def _handle_status(self, options):
        return validate_whatsapp_web_login(**self._browser_options(options))

    def _handle_send(self, options):
        return send_whatsapp_web_message(
            phone=options["to"],
            message=options["message"],
            default_country_code=options["country_code"],
            **self._browser_options(options),
        )

    def _handle_read(self, options):
        since = parse_cli_date(options["since"])
        until = parse_cli_date(options["until"])
        exact_date = parse_cli_date(options["date"])
        if exact_date:
            since = exact_date
            until = exact_date
        return read_whatsapp_web_messages(
            phone=options["from_phone"],
            default_country_code=options["country_code"],
            since=since,
            until=until,
            only_new=options["new"],
            update_cursor=not options["no_update_cursor"],
            limit=options["limit"],
            **self._browser_options(options),
        )

    def _write_text_result(self, result) -> None:
        payload = dataclass_payload(result)
        messages = payload.pop("messages", None)
        for key, value in payload.items():
            if value is not None:
                self.stdout.write(f"{key}={value}")
        if messages is not None:
            for message in messages:
                self.stdout.write(json.dumps(message, ensure_ascii=False))
