"""Run upgrade diagnostics and channel operations from the CLI."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.services.upgrade_notifications import notify_upgrade_completion
from apps.core.system.filesystem import _clear_auto_upgrade_skip_revisions
from apps.core.system.upgrade import (
    UPGRADE_CHANNEL_CHOICES,
    _build_auto_upgrade_report,
    _set_upgrade_policy_channel,
    _trigger_upgrade_check,
)


class Command(BaseCommand):
    """Expose upgrade report, checks, and channel controls in one command."""

    help = (
        "Inspect upgrade status, run manual upgrade checks, and switch the "
        "configured upgrade channel for local policies."
    )

    def add_arguments(self, parser):
        """Register subcommands and command-specific options."""

        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        show_parser = subparsers.add_parser(
            "show",
            help="Display upgrade status details shown in the admin report.",
        )
        show_parser.add_argument(
            "--log-limit",
            type=int,
            default=15,
            help="Maximum number of recent log entries to display.",
        )

        check_parser = subparsers.add_parser(
            "check",
            help="Trigger an upgrade check using the selected channel override.",
        )
        check_parser.add_argument(
            "--channel",
            default="stable",
            help="Channel override for this check (stable, unstable, latest).",
        )
        check_parser.add_argument(
            "--keep-skip-revisions",
            action="store_true",
            help="Do not clear blocked-revision lockfile before triggering the check.",
        )

        channel_parser = subparsers.add_parser(
            "channel",
            help="Switch assigned local upgrade policies to a channel.",
        )
        channel_parser.add_argument(
            "channel",
            help="Target channel (stable, unstable, latest).",
        )

        notify_parser = subparsers.add_parser(
            "notify",
            help="Send the post-upgrade status email and rotate the default admin temp password.",
        )
        notify_parser.add_argument(
            "--exit-status",
            type=int,
            default=0,
            help="Exit status from the completed upgrade script.",
        )
        notify_parser.add_argument(
            "--source",
            default="upgrade.sh",
            help="Source command or workflow that completed the upgrade.",
        )
        notify_parser.add_argument(
            "--channel",
            default="",
            help="Upgrade channel used for the completed run.",
        )
        notify_parser.add_argument(
            "--branch",
            default="",
            help="Git branch associated with the completed run.",
        )
        notify_parser.add_argument(
            "--service",
            default="",
            help="Service name for the upgraded instance when known.",
        )
        notify_parser.add_argument(
            "--initial-version",
            default="",
            help="Version recorded before the upgrade started.",
        )
        notify_parser.add_argument(
            "--target-version",
            default="",
            help="Version observed as the intended target before the upgrade ran.",
        )
        notify_parser.add_argument(
            "--initial-revision",
            default="",
            help="Git revision recorded before the upgrade started.",
        )
        notify_parser.add_argument(
            "--target-revision",
            default="",
            help="Git revision observed as the intended target before the upgrade ran.",
        )

    def handle(self, *args, **options):
        """Dispatch to the selected upgrade command action."""

        action = options["action"]
        handler = getattr(self, f"_handle_{action}", None)
        if handler and callable(handler):
            handler(options)
            return

        raise CommandError(f"Unsupported upgrade action: {action}")

    def _handle_show(self, options: dict[str, object]) -> None:
        """Print a text summary of the admin upgrade report data."""

        limit = options["log_limit"]
        if limit <= 0:
            raise CommandError("--log-limit must be a positive integer.")

        report = _build_auto_upgrade_report(limit=limit)
        summary = report.get("summary", {})
        settings_info = report.get("settings", {})
        schedule = report.get("schedule", {})
        log_entries = report.get("log_entries") or []

        self.stdout.write(f"Status: {summary.get('state', 'unknown')}")
        self.stdout.write(f"Headline: {summary.get('headline', 'Unavailable')}")
        self.stdout.write(f"Mode: {settings_info.get('channels') or ['manual']}")
        self.stdout.write(f"Next run: {schedule.get('next_run') or 'Unavailable'}")
        self.stdout.write(f"Last run: {schedule.get('last_run_at') or 'Unavailable'}")
        self.stdout.write(f"Failure count: {schedule.get('failure_count', 0)}")

        issues = summary.get("issues") or []
        if issues:
            self.stdout.write("Issues:")
            for issue in issues:
                self.stdout.write(f" - [{issue.get('severity', 'warning')}] {issue.get('label', '')}")
        else:
            self.stdout.write("Issues: none")

        blocked_revisions = settings_info.get("skip_revisions") or []
        if blocked_revisions:
            self.stdout.write("Blocked revisions:")
            for revision in blocked_revisions:
                self.stdout.write(f" - {revision}")
        else:
            self.stdout.write("Blocked revisions: none")

        self.stdout.write("Recent activity:")
        if log_entries:
            for entry in log_entries:
                timestamp = entry.get("timestamp") or "Unknown"
                message = entry.get("message") or ""
                self.stdout.write(f" - {timestamp} {message}")
        else:
            self.stdout.write(" - No recent auto-upgrade activity.")

    def _handle_check(self, options: dict[str, object]) -> None:
        """Trigger an upgrade check with optional per-run channel override."""

        requested_channel = options["channel"].strip().lower()
        channel_choice = UPGRADE_CHANNEL_CHOICES.get(requested_channel)
        if not channel_choice:
            available_channels = ", ".join(sorted(UPGRADE_CHANNEL_CHOICES.keys()))
            raise CommandError(
                f"Unsupported channel '{requested_channel}'. Available: {available_channels}."
            )

        override_value = channel_choice.get("override")
        channel_override = override_value if isinstance(override_value, str) else None
        if channel_override == "stable":
            channel_override = None

        if not options["keep_skip_revisions"]:
            _clear_auto_upgrade_skip_revisions(Path(settings.BASE_DIR))

        queued = _trigger_upgrade_check(channel_override=channel_override)
        if queued:
            self.stdout.write(self.style.SUCCESS("Upgrade check queued."))
        else:
            self.stdout.write(self.style.SUCCESS("Upgrade check started locally."))

    def _handle_channel(self, options: dict[str, object]) -> None:
        """Persist a channel change for local-node assigned upgrade policies."""

        requested_channel = options["channel"].strip().lower()
        result = _set_upgrade_policy_channel(requested_channel)
        if not bool(result.get("ok")):
            message = str(result.get("message") or "Unable to update upgrade channel.")
            raise CommandError(message)

        channel = str(result.get("channel") or requested_channel)
        updated = int(result.get("updated") or 0)
        self.stdout.write(
            self.style.SUCCESS(
                f"Upgrade channel set to '{channel}' for {updated} assigned policy"
                f"{'ies' if updated != 1 else ''}."
            )
        )

    def _handle_notify(self, options: dict[str, object]) -> None:
        """Send the post-upgrade status email and rotate the default admin temp password."""

        result = notify_upgrade_completion(
            base_dir=Path(settings.BASE_DIR),
            exit_status=int(options["exit_status"]),
            source=str(options.get("source") or "upgrade.sh").strip() or "upgrade.sh",
            channel=str(options.get("channel") or "").strip() or None,
            branch=str(options.get("branch") or "").strip() or None,
            service_name=str(options.get("service") or "").strip() or None,
            initial_version=str(options.get("initial_version") or "").strip() or None,
            target_version=str(options.get("target_version") or "").strip() or None,
            initial_revision=str(options.get("initial_revision") or "").strip() or None,
            target_revision=str(options.get("target_revision") or "").strip() or None,
        )
        if not result.email_sent:
            raise CommandError(
                result.error or "Upgrade notification email was not sent."
            )

        expires_at = result.expires_at.isoformat() if result.expires_at else "unknown"
        recipients = ", ".join(result.recipients) or "<none>"
        self.stdout.write(
            self.style.SUCCESS(
                f"Upgrade notification sent to {recipients}; "
                f"temporary password rotated for {result.admin_username} "
                f"(expires {expires_at})."
            )
        )
