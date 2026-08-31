"""Management command for the Arthexis readiness slogan and report."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.good import build_good_report, docs_url, marketing_tagline


class Command(BaseCommand):
    """Report how good the current Arthexis deployment looks.

    The command emits only ``Arthexis is Good`` when no issues are found, emits
    ``Arthexis is Good*`` when only minor non-error considerations exist, and
    prints a ranked issue list when important issues need attention.
    """

    help = "Assess whether the current Arthexis setup looks good enough for production use."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: Django's argument parser instance.
        """

        parser.add_argument(
            "--details",
            action="store_true",
            help="Show minor considerations even when the setup is otherwise good.",
        )
        parser.add_argument(
            "--tagline",
            action="store_true",
            help="Print the recommended marketing tagline for the command and exit.",
        )

    def handle(self, *args, **options) -> None:
        """Execute the command.

        Args:
            *args: Positional arguments from Django.
            **options: Parsed command options.
        """

        if options["tagline"]:
            self.stdout.write(marketing_tagline(docs_url=docs_url()))
            return

        report = build_good_report()
        show_details = bool(options["details"])

        if not report.has_issues:
            self.stdout.write(report.success_line)
            return

        if not report.has_non_minor_issues and not show_details:
            self.stdout.write(report.success_line)
            return

        if not report.has_non_minor_issues and show_details:
            self.stdout.write(report.success_line)
            self.stdout.write("")
            self.stdout.write("Minor considerations:")
        else:
            self.stdout.write("Issues to consider (highest priority first):")

        for index, issue in enumerate(report.issues, start=1):
            style = self._style_for_issue(issue.severity)
            severity = issue.severity.upper()
            self.stdout.write(style(f"{index}. [{severity}] {issue.title}"))
            self.stdout.write(f"   Category: {issue.category}")
            self.stdout.write(f"   {issue.detail}")

    def _style_for_issue(self, severity: str):
        """Return the Django terminal styling function for ``severity``.

        Args:
            severity: Severity level name.

        Returns:
            A callable that styles the title line.
        """

        if severity in {"critical", "important"}:
            return self.style.ERROR
        if severity == "warning":
            return self.style.WARNING
        return self.style.NOTICE
