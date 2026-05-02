from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.meta.services import (
    DEFAULT_WHATSAPP_SECRETARY_IDLE_AFTER_SECONDS,
    DEFAULT_WHATSAPP_SECRETARY_POLL_SECONDS,
    DEFAULT_WHATSAPP_SECRETARY_QUIET_SECONDS,
    DEFAULT_WHATSAPP_SECRETARY_TRIGGER_PREFIX,
    DEFAULT_WHATSAPP_WEB_BROWSER,
    DEFAULT_WHATSAPP_WEB_CHANNEL,
    DEFAULT_WHATSAPP_WEB_PROFILE_DIR,
    build_whatsapp_listener_install_plan,
    dataclass_payload,
    listen_for_whatsapp_secretary_requests,
    parse_cli_date,
    read_whatsapp_web_messages,
    send_whatsapp_web_message,
    validate_whatsapp_web_login,
)


class Command(BaseCommand):
    help = "WhatsApp Web login, send, read, and local listener commands."

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

        listen = subparsers.add_parser(
            "listen",
            help="Poll an operator self-chat and launch a Codex Secretary terminal.",
        )
        self._add_browser_arguments(listen, default_timeout=120.0)
        listen.add_argument(
            "--from",
            dest="from_phone",
            required=True,
            help="Operator self-chat phone number.",
        )
        listen.add_argument(
            "--country-code",
            default="52",
            help="Country code for 10-digit local numbers. Default: 52.",
        )
        listen.add_argument(
            "--trigger-prefix",
            default=DEFAULT_WHATSAPP_SECRETARY_TRIGGER_PREFIX,
            help=(
                "Message prefix required to launch Secretary. Use an empty value "
                "to process every new message. Default: secretary:"
            ),
        )
        listen.add_argument(
            "--idle-after",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_IDLE_AFTER_SECONDS,
            help=(
                "Desktop idle seconds required before polling (Windows only); "
                "0 disables. Default: 300."
            ),
        )
        listen.add_argument(
            "--poll-every",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_POLL_SECONDS,
            help="Seconds between WhatsApp read polls. Default: 60.",
        )
        listen.add_argument(
            "--quiet-window",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_QUIET_SECONDS,
            help="Seconds without new messages required before processing. Default: 60.",
        )
        listen.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum visible new messages to inspect per poll; 0 means all.",
        )
        listen.add_argument(
            "--codex-command",
            default="codex",
            help="Codex executable command used in the launched terminal.",
        )
        listen.add_argument(
            "--secretary-name",
            default="Secretary",
            help="Nickname used in the generated SECRETARY prompt.",
        )
        listen.add_argument(
            "--terminal-title",
            default="Arthexis Secretary",
            help="Window/tab title for the launched terminal.",
        )
        listen.add_argument(
            "--once",
            action="store_true",
            help="Exit after processing one quiet message batch.",
        )
        listen.add_argument(
            "--no-launch",
            action="store_true",
            help="Match and advance the cursor without launching Codex.",
        )
        listen.add_argument("--json", action="store_true", help="Emit JSON output.")

        install = subparsers.add_parser(
            "install-listener",
            help="Plan or write OS startup artifacts for WhatsApp listener mode.",
        )
        self._add_browser_arguments(
            install,
            default_timeout=120.0,
            default_browser=None,
            default_channel=None,
            browser_help=(
                "Browser engine to place in the generated listener command. "
                "Defaults from --platform."
            ),
            channel_help=(
                "Optional Playwright Chromium channel. Defaults to msedge for "
                "Windows Edge plans."
            ),
        )
        install.add_argument(
            "--from",
            dest="from_phone",
            required=True,
            help="Operator self-chat phone number.",
        )
        install.add_argument(
            "--country-code",
            default="52",
            help="Country code for 10-digit local numbers. Default: 52.",
        )
        install.add_argument(
            "--trigger-prefix",
            default=DEFAULT_WHATSAPP_SECRETARY_TRIGGER_PREFIX,
            help="Message prefix required to launch Secretary. Default: secretary:",
        )
        install.add_argument(
            "--idle-after",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_IDLE_AFTER_SECONDS,
            help="Desktop idle seconds required before polling. Default: 300.",
        )
        install.add_argument(
            "--poll-every",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_POLL_SECONDS,
            help="Seconds between WhatsApp read polls. Default: 60.",
        )
        install.add_argument(
            "--quiet-window",
            type=float,
            default=DEFAULT_WHATSAPP_SECRETARY_QUIET_SECONDS,
            help="Seconds without new messages required before processing. Default: 60.",
        )
        install.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum visible new messages to inspect per poll; 0 means all.",
        )
        install.add_argument(
            "--codex-command",
            default="codex",
            help="Codex executable command used in the launched terminal.",
        )
        install.add_argument(
            "--secretary-name",
            default="Secretary",
            help="Nickname used in the generated SECRETARY prompt.",
        )
        install.add_argument(
            "--terminal-title",
            default="Arthexis Secretary",
            help="Window/tab title for the launched terminal.",
        )
        install.add_argument(
            "--platform",
            choices=("windows", "win32", "linux"),
            help="Override target platform for generated artifacts.",
        )
        install.add_argument(
            "--service-name",
            default="arthexis-whatsapp-listener",
            help="Scheduled Task or systemd unit base name.",
        )
        install.add_argument(
            "--output-dir",
            help="Directory for generated listener runner files.",
        )
        install.add_argument(
            "--systemd-user-dir",
            help="Linux systemd user unit directory. Default: ~/.config/systemd/user.",
        )
        install.add_argument(
            "--base-dir",
            help=(
                "Target suite checkout directory for the generated runner. "
                "Defaults to this checkout, or to a target-platform convention "
                "for cross-platform plans."
            ),
        )
        install.add_argument(
            "--python",
            dest="python_executable",
            help="Python executable to place in the generated listener command.",
        )
        install.add_argument(
            "--manage-py",
            help="Path to manage.py. Defaults to this suite checkout.",
        )
        install.add_argument(
            "--write",
            action="store_true",
            help="Write helper files. Without this, only print the install plan.",
        )
        install.add_argument("--json", action="store_true", help="Emit JSON output.")

    def _add_browser_arguments(
        self,
        parser,
        *,
        default_timeout: float,
        default_browser: str | None = DEFAULT_WHATSAPP_WEB_BROWSER,
        default_channel: str | None = DEFAULT_WHATSAPP_WEB_CHANNEL,
        browser_help: str | None = None,
        channel_help: str | None = None,
    ) -> None:
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
            default=default_browser,
            choices=("edge", "firefox", "chromium"),
            help=browser_help
            or "Browser engine to use. Defaults to Edge on Windows and Firefox elsewhere.",
        )
        parser.add_argument(
            "--channel",
            default=default_channel,
            help=channel_help or "Optional Playwright Chromium channel, for example msedge.",
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
            elif action == "listen":
                return self._handle_listen(options)
            elif action == "install-listener":
                return self._handle_install_listener(options)
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
            "browser": options.get("browser") or "",
            "channel": (options.get("channel") or "").strip(),
            "cdp_url": (options.get("cdp_url") or "").strip(),
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
        if options["limit"] < 0:
            raise CommandError("--limit must be >= 0. Use 0 to return all visible messages.")
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

    def _handle_listen(self, options):
        if options["limit"] < 0:
            raise CommandError("--limit must be >= 0. Use 0 to return all visible messages.")
        if options["idle_after"] < 0:
            raise CommandError("--idle-after must be >= 0.")
        if options["poll_every"] < 1:
            raise CommandError("--poll-every must be >= 1.")
        if options["quiet_window"] < 1:
            raise CommandError("--quiet-window must be >= 1.")

        def write_event(result) -> None:
            if options.get("json"):
                self.stdout.write(json.dumps(dataclass_payload(result), indent=2))
            else:
                self._write_text_result(result)

        results = listen_for_whatsapp_secretary_requests(
            phone=options["from_phone"],
            default_country_code=options["country_code"],
            trigger_prefix=options["trigger_prefix"],
            idle_after_seconds=options["idle_after"],
            daemon_poll_seconds=options["poll_every"],
            quiet_window_seconds=options["quiet_window"],
            limit=options["limit"],
            launch=not options["no_launch"],
            codex_command=options["codex_command"],
            secretary_name=options["secretary_name"],
            terminal_title=options["terminal_title"],
            max_batches=1 if options["once"] else None,
            event_callback=None if options["once"] else write_event,
            **self._browser_options(options),
        )
        if options["once"] and results:
            write_event(results[-1])
        return None

    def _handle_install_listener(self, options):
        if options["limit"] < 0:
            raise CommandError("--limit must be >= 0. Use 0 to return all visible messages.")
        if options["idle_after"] < 0:
            raise CommandError("--idle-after must be >= 0.")
        if options["poll_every"] < 1:
            raise CommandError("--poll-every must be >= 1.")
        if options["quiet_window"] < 1:
            raise CommandError("--quiet-window must be >= 1.")

        plan = build_whatsapp_listener_install_plan(
            phone=options["from_phone"],
            default_country_code=options["country_code"],
            trigger_prefix=options["trigger_prefix"],
            idle_after_seconds=options["idle_after"],
            daemon_poll_seconds=options["poll_every"],
            quiet_window_seconds=options["quiet_window"],
            limit=options["limit"],
            codex_command=options["codex_command"],
            secretary_name=options["secretary_name"],
            terminal_title=options["terminal_title"],
            platform=options["platform"],
            base_dir=options["base_dir"],
            output_dir=options["output_dir"],
            systemd_user_dir=options["systemd_user_dir"],
            python_executable=options["python_executable"],
            manage_py=options["manage_py"],
            service_name=options["service_name"],
            write_files=options["write"],
            **self._browser_options(options),
        )
        if options.get("json"):
            self.stdout.write(json.dumps(dataclass_payload(plan), indent=2))
        else:
            self._write_install_plan(plan)
        return None

    def _write_install_plan(self, plan) -> None:
        payload = dataclass_payload(plan)
        for key in (
            "status",
            "platform",
            "service_name",
            "base_dir",
            "profile_dir",
            "output_dir",
            "runner_path",
            "service_path",
            "wrote_files",
            "detail",
        ):
            self.stdout.write(f"{key}={payload[key]}")
        self.stdout.write("listen_command:")
        self.stdout.write(str(payload["listen_command"]))
        self.stdout.write("requirements:")
        for requirement in payload["requirements"]:
            self.stdout.write(f"- {requirement}")
        self.stdout.write("manual_commands:")
        for key in (
            "install_command",
            "start_command",
            "status_command",
            "stop_command",
            "uninstall_command",
        ):
            self.stdout.write(f"{key}={payload[key]}")
        self.stdout.write("instructions:")
        for instruction in payload["instructions"]:
            self.stdout.write(f"- {instruction}")

    def _write_text_result(self, result) -> None:
        payload = dataclass_payload(result)
        messages = payload.pop("messages", None)
        for key, value in payload.items():
            if value is not None:
                self.stdout.write(f"{key}={value}")
        if messages is not None:
            for message in messages:
                self.stdout.write(json.dumps(message, ensure_ascii=False))
