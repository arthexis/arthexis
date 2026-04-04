"""Emit a review-ready notification through the LCD or fallback notification."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.review_notifications import (
    DEFAULT_REVIEW_NOTIFICATION_EXPIRY_SECONDS,
    send_review_notification,
)


class Command(BaseCommand):
    help = "Send a review-ready notification through the LCD or fallback notification"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--actor",
            default="Codex",
            help="Short actor label shown on the review notification.",
        )
        parser.add_argument(
            "--summary",
            default=None,
            help="Optional short second-line summary override.",
        )
        parser.add_argument(
            "--expires-in",
            default=DEFAULT_REVIEW_NOTIFICATION_EXPIRY_SECONDS,
            type=int,
            help="Optional expiry in seconds. Use 0 to keep the message until overwritten.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send the notification even when git reports no reviewable changes.",
        )

    def handle(self, *args, **options) -> None:
        if options["expires_in"] < 0:
            raise CommandError("--expires-in must be greater than or equal to 0.")

        result = send_review_notification(
            actor=options["actor"],
            summary=options["summary"],
            expires_in=options["expires_in"],
            force=options["force"],
        )

        if result.skipped:
            branch = result.branch or "unknown"
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped review notification: no reviewable git changes detected "
                    f"(branch={branch})."
                )
            )
            return

        branch = result.branch or "unknown"
        changed_files = (
            "unknown"
            if result.changed_file_count is None
            else str(result.changed_file_count)
        )
        if result.used_lcd:
            transport = "LCD"
        else:
            transport = "fallback notification (LCD unavailable)"

        self.stdout.write(
            self.style.SUCCESS(
                f"Review notification sent via {transport}: "
                f"subject='{result.subject}' body='{result.body}' "
                f"(branch={branch}, changed_files={changed_files})"
            )
        )
