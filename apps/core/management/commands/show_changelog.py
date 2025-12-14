from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core import changelog
from apps.core.system import _format_timestamp


class Command(BaseCommand):
    """Display recent changelog entries from the latest section."""

    help = "Show the most recent changelog entries (unreleased by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "-n",
            "--n",
            type=int,
            default=5,
            help="Number of changelog entries to display.",
        )

    def handle(self, *args, **options):
        limit = options["n"]
        if limit < 1:
            raise CommandError("--n must be a positive integer.")

        try:
            page = changelog.get_initial_page(initial_count=1)
        except changelog.ChangelogError as exc:
            raise CommandError(f"Unable to load changelog data: {exc}") from exc

        if not page.sections:
            self.stdout.write(self.style.WARNING("No changelog information is available."))
            return

        latest_section = page.sections[0]
        commits = latest_section.commits[:limit]

        section_label = latest_section.title
        if latest_section.is_unreleased:
            section_label = f"{section_label} (unreleased)"
        elif latest_section.version:
            section_label = f"{section_label} [{latest_section.version}]"

        self.stdout.write(self.style.MIGRATE_HEADING(f"Latest changelog section: {section_label}"))
        self.stdout.write(
            f"Showing {len(commits)} of {len(latest_section.commits)} entries from the most recent section."
        )

        if not commits:
            self.stdout.write(self.style.WARNING("No commits found in the latest section."))
            return

        for commit in commits:
            timestamp = _format_timestamp(commit.authored_at)
            description = f"[{commit.short_sha}] {commit.summary}"
            if commit.author:
                description = f"{description} — {commit.author}"
            if commit.commit_url:
                description = f"{description} ({commit.commit_url})"
            description = f"{description} [{timestamp}]"
            self.stdout.write(description)
