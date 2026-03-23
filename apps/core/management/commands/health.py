from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.services.health import (
    HealthCheckDefinition,
    HealthExitCode,
    resolve_targets,
    run_health_checks,
)
from apps.core.services.health_checks import (
    run_check_admin,
    run_check_lcd_send,
    run_check_lcd_service,
    run_check_next_upgrade,
    run_check_rfid,
    run_check_system_user,
    run_check_time,
)
from apps.ocpp.services.health_checks import run_check_forwarders
from apps.release.services.health_checks import run_check_pypi


HEALTH_CHECKS = {
    "core.admin": HealthCheckDefinition(
        target="core.admin",
        group="core",
        description="Verify default admin account health",
        runner=run_check_admin,
    ),
    "core.lcd_send": HealthCheckDefinition(
        target="core.lcd_send",
        group="core",
        description="Send and validate an LCD lock-file message",
        runner=run_check_lcd_send,
        include_in_group=False,
    ),
    "core.lcd_service": HealthCheckDefinition(
        target="core.lcd_service",
        group="core",
        description="Validate LCD service readiness",
        runner=run_check_lcd_service,
        include_in_group=False,
    ),
    "core.next_upgrade": HealthCheckDefinition(
        target="core.next_upgrade",
        group="core",
        description="Inspect auto-upgrade scheduling",
        runner=run_check_next_upgrade,
    ),
    "core.rfid": HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=run_check_rfid,
        include_in_group=False,
    ),
    "core.system_user": HealthCheckDefinition(
        target="core.system_user",
        group="core",
        description="Verify system account health",
        runner=run_check_system_user,
    ),
    "core.time": HealthCheckDefinition(
        target="core.time",
        group="core",
        description="Display current server time",
        runner=run_check_time,
    ),
    "ocpp.forwarders": HealthCheckDefinition(
        target="ocpp.forwarders",
        group="ocpp",
        description="Report OCPP forwarding diagnostics",
        runner=run_check_forwarders,
    ),
    "release.pypi": HealthCheckDefinition(
        target="release.pypi",
        group="release",
        description="Check PyPI publishing readiness",
        runner=run_check_pypi,
        include_in_group=False,
    ),
}


class Command(BaseCommand):
    """Run one or more application health checks from a unified interface."""

    help = "Run one or more health checks by --target and/or --group."

    def add_arguments(self, parser):
        parser.add_argument(
            "--target",
            action="append",
            default=[],
            help="Target in <app>.<check> form (e.g. --target core.time). Can be repeated.",
        )
        parser.add_argument(
            "--group",
            action="append",
            default=[],
            help="Run all non-interactive checks in a group (e.g. --group core).",
        )
        parser.add_argument("--all", action="store_true", help="Run all grouped checks.")
        parser.add_argument("--list-targets", action="store_true", help="List available targets.")
        parser.add_argument("--force", action="store_true", help="Auto-repair where supported.")

        parser.add_argument("--release", default=None, help="Release PK or version for release.pypi.")
        parser.add_argument("--rfid-value", default=None, help="RFID value for core.rfid.")
        parser.add_argument("--rfid-kind", default=None, help="Optional RFID kind for core.rfid.")
        parser.add_argument("--rfid-pretty", action="store_true", help="Pretty-print RFID JSON output.")

        parser.add_argument("--lcd-subject", default=None, help="LCD subject for core.lcd_send.")
        parser.add_argument("--lcd-body", default="", help="LCD body for core.lcd_send.")
        parser.add_argument("--lcd-expires-at", default=None, help="Expiration for core.lcd_send.")
        parser.add_argument("--lcd-sticky", action="store_true", help="Use sticky LCD lock for core.lcd_send.")
        parser.add_argument("--lcd-channel-type", default=None, help="LCD channel type for core.lcd_send.")
        parser.add_argument("--lcd-channel-num", default=None, help="LCD channel number for core.lcd_send.")
        parser.add_argument("--lcd-timeout", type=float, default=10.0, help="Timeout for LCD checks.")
        parser.add_argument(
            "--lcd-poll-interval", type=float, default=0.2, help="Polling interval for LCD checks."
        )
        parser.add_argument(
            "--lcd-confirmed",
            action="store_true",
            help="Treat LCD display confirmation as passed for core.lcd_service.",
        )

    def handle(self, *args, **options):
        if options["list_targets"]:
            for definition in sorted(HEALTH_CHECKS.values(), key=lambda item: item.target):
                grouped = "yes" if definition.include_in_group else "no"
                self.stdout.write(
                    f"{definition.target} [group={definition.group}, grouped={grouped}] - {definition.description}"
                )
            return

        groups = list(options.get("group") or [])
        targets = list(options.get("target") or [])
        if options.get("all"):
            groups.extend(sorted({item.group for item in HEALTH_CHECKS.values()}))
        if not groups and not targets:
            raise CommandError("Specify at least one --target, --group, or --all.")

        definitions, unknown = resolve_targets(
            available_targets=HEALTH_CHECKS,
            targets=targets,
            groups=groups,
        )
        if unknown:
            raise CommandError(f"Unknown target/group selector(s): {', '.join(unknown)}")

        exit_code = run_health_checks(
            definitions=definitions,
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
            options={
                "force": bool(options.get("force")),
                "release_identifier": options.get("release"),
                "rfid_value": options.get("rfid_value"),
                "rfid_kind": options.get("rfid_kind"),
                "rfid_pretty": bool(options.get("rfid_pretty")),
                "lcd_subject": options.get("lcd_subject"),
                "lcd_body": options.get("lcd_body", ""),
                "lcd_expires_at": options.get("lcd_expires_at"),
                "lcd_sticky": bool(options.get("lcd_sticky")),
                "lcd_channel_type": options.get("lcd_channel_type"),
                "lcd_channel_num": options.get("lcd_channel_num"),
                "lcd_timeout": float(options.get("lcd_timeout", 10.0)),
                "lcd_poll_interval": float(options.get("lcd_poll_interval", 0.2)),
                "lcd_confirmed": bool(options.get("lcd_confirmed")),
            },
        )
        if exit_code == HealthExitCode.OK:
            self.stdout.write(self.style.SUCCESS("Health checks passed."))
            return
        if exit_code == HealthExitCode.CHECK_FAILED:
            raise SystemExit(int(HealthExitCode.CHECK_FAILED))
        raise SystemExit(int(HealthExitCode.INVALID_TARGET))
