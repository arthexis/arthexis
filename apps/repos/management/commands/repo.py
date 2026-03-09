"""Operate on repositories, issues, pull requests, and releases."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.release import DEFAULT_PACKAGE
from apps.repos.release_management import (
    EXECUTION_MODE_BINARY,
    EXECUTION_MODE_SUITE,
    ReleaseManagementClient,
    ReleaseManagementError,
    RepositoryRef,
)


class Command(BaseCommand):
    """Expose Release Management operations for CLI users."""

    help = "Operate on GitHub issues, pull requests, and releases via Release Management."

    def add_arguments(self, parser):
        parser.add_argument(
            "--repo",
            default="",
            help=(
                "Repository slug in owner/name format. Defaults to active package repository "
                f"or {DEFAULT_PACKAGE.repository_url}."
            ),
        )
        parser.add_argument(
            "--mode",
            choices=(EXECUTION_MODE_SUITE, EXECUTION_MODE_BINARY),
            help="Execution mode: suite (token/API first) or binary (gh/git first).",
        )

        subparsers = parser.add_subparsers(dest="resource", required=True)

        issues_parser = subparsers.add_parser("issues", help="Issue operations")
        issues_subparsers = issues_parser.add_subparsers(dest="action", required=True)
        issues_list = issues_subparsers.add_parser("list", help="List issues")
        issues_list.add_argument("--state", default="open")
        issues_create = issues_subparsers.add_parser("create", help="Create issue")
        issues_create.add_argument("--title", required=True)
        issues_create.add_argument("--body", required=True)

        prs_parser = subparsers.add_parser("prs", help="Pull request operations")
        prs_subparsers = prs_parser.add_subparsers(dest="action", required=True)
        prs_list = prs_subparsers.add_parser("list", help="List pull requests")
        prs_list.add_argument("--state", default="open")

        releases_parser = subparsers.add_parser("releases", help="Release operations")
        releases_subparsers = releases_parser.add_subparsers(dest="action", required=True)
        releases_list = releases_subparsers.add_parser("list", help="List releases")
        releases_list.add_argument("--limit", type=int, default=20)
        releases_create = releases_subparsers.add_parser("create", help="Create release")
        releases_create.add_argument("--tag", required=True)
        releases_create.add_argument("--title", required=True)
        releases_create.add_argument("--notes", default="")

    def handle(self, *args, **options):
        repository = self._resolve_repository(str(options.get("repo") or ""))
        client = ReleaseManagementClient(mode=options.get("mode"))

        resource = options["resource"]
        action = options["action"]

        try:
            if resource == "issues" and action == "list":
                rows = client.list_issues(repository, state=str(options.get("state") or "open"))
                for item in rows:
                    self.stdout.write(f"#{item.get('number')} [{item.get('state')}] {item.get('title')}")
                self.stdout.write(self.style.SUCCESS(f"Listed {len(rows)} issues from {repository.slug}"))
                return

            if resource == "issues" and action == "create":
                url = client.create_issue(
                    repository,
                    title=str(options["title"]),
                    body=str(options["body"]),
                )
                self.stdout.write(self.style.SUCCESS(f"Issue created: {url}"))
                return

            if resource == "prs" and action == "list":
                rows = client.list_pull_requests(repository, state=str(options.get("state") or "open"))
                for item in rows:
                    self.stdout.write(f"#{item.get('number')} [{item.get('state')}] {item.get('title')}")
                self.stdout.write(
                    self.style.SUCCESS(f"Listed {len(rows)} pull requests from {repository.slug}")
                )
                return

            if resource == "releases" and action == "list":
                rows = client.list_releases(repository, limit=int(options.get("limit") or 20))
                for item in rows:
                    self.stdout.write(
                        f"{item.get('tagName')} - {item.get('name') or ''} ({item.get('url') or ''})"
                    )
                self.stdout.write(
                    self.style.SUCCESS(f"Listed {len(rows)} releases from {repository.slug}")
                )
                return

            if resource == "releases" and action == "create":
                response = client.create_release(
                    repository,
                    tag=str(options["tag"]),
                    title=str(options["title"]),
                    notes=str(options.get("notes") or ""),
                )
                self.stdout.write(self.style.SUCCESS(response or "Release created"))
                return
        except ReleaseManagementError as exc:
            raise CommandError(str(exc)) from exc

        raise CommandError(f"Unsupported command: {resource} {action}")

    def _resolve_repository(self, raw_repo: str) -> RepositoryRef:
        from apps.repos.github import parse_repository_url, resolve_active_repository

        cleaned = raw_repo.strip()
        if cleaned:
            if "://" in cleaned or cleaned.startswith("git@"):
                try:
                    owner, name = parse_repository_url(cleaned)
                except ValueError as exc:
                    raise CommandError(str(exc)) from exc
            else:
                parts = cleaned.split("/", 1)
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    raise CommandError("Repository must be in owner/name format")
                owner, name = parts[0].strip(), parts[1].strip()
            return RepositoryRef(owner=owner, name=name)

        active = resolve_active_repository()
        return RepositoryRef(owner=active.owner, name=active.name)
