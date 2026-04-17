"""Operate on repositories, issues, pull requests, and releases."""

from __future__ import annotations

from argparse import RawDescriptionHelpFormatter
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError

from apps.release import DEFAULT_PACKAGE
from apps.repos.release_management import (
    EXECUTION_MODE_BINARY,
    EXECUTION_MODE_SUITE,
    MERGE_METHOD_CHOICES,
    ReleaseManagementClient,
    ReleaseManagementError,
    RepositoryRef,
)


class Command(BaseCommand):
    """Expose Release Management operations for CLI users."""

    help = "Operate on GitHub issues, pull requests, and releases via Release Management."

    def add_arguments(self, parser):
        """Register CLI arguments for repository operations.

        Parameters:
            parser: Django's argument parser for this management command.

        Returns:
            None.
        """

        parser.formatter_class = RawDescriptionHelpFormatter
        parser.epilog = dedent(
            """
            Examples:
              repo issues list --repo octo/demo
              repo issues show 123 --repo octo/demo
              repo issues comment 123 --body "Please attach logs." --repo octo/demo
              repo issues close 123 --repo octo/demo
              repo prs list --repo octo/demo
              repo prs show 456 --repo octo/demo
              repo prs comment 456 --body "Please rebase." --repo octo/demo
              repo prs ready 456 --repo octo/demo
              repo prs merge 456 --method squash --repo octo/demo
              repo releases create --repo octo/demo --tag v1.2.3 --title "Release v1.2.3"
              repo issues list --repo octo/demo
            """
        ).strip()
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
        self._add_repository_argument(issues_list)
        issues_show = issues_subparsers.add_parser(
            "show", help="Show issue activity and reactions"
        )
        issues_show.add_argument("number", type=int)
        self._add_repository_argument(issues_show)
        issues_comment = issues_subparsers.add_parser("comment", help="Comment on issue")
        issues_comment.add_argument("number", type=int)
        issues_comment.add_argument("--body", required=True)
        self._add_repository_argument(issues_comment)
        issues_close = issues_subparsers.add_parser("close", help="Close issue")
        issues_close.add_argument("number", type=int)
        self._add_repository_argument(issues_close)
        issues_create = issues_subparsers.add_parser("create", help="Create issue")
        issues_create.add_argument("--title", required=True)
        issues_create.add_argument("--body", required=True)
        self._add_repository_argument(issues_create)

        prs_parser = subparsers.add_parser("prs", help="Pull request operations")
        prs_subparsers = prs_parser.add_subparsers(dest="action", required=True)
        prs_list = prs_subparsers.add_parser("list", help="List pull requests")
        prs_list.add_argument("--state", default="open")
        self._add_repository_argument(prs_list)
        prs_show = prs_subparsers.add_parser(
            "show", help="Show pull request activity and reactions"
        )
        prs_show.add_argument("number", type=int)
        self._add_repository_argument(prs_show)
        prs_comment = prs_subparsers.add_parser("comment", help="Comment on pull request")
        prs_comment.add_argument("number", type=int)
        prs_comment.add_argument("--body", required=True)
        self._add_repository_argument(prs_comment)
        prs_ready = prs_subparsers.add_parser(
            "ready", help="Move pull request out of draft"
        )
        prs_ready.add_argument("number", type=int)
        self._add_repository_argument(prs_ready)
        prs_merge = prs_subparsers.add_parser("merge", help="Merge pull request")
        prs_merge.add_argument("number", type=int)
        prs_merge.add_argument(
            "--method",
            choices=MERGE_METHOD_CHOICES,
            default=MERGE_METHOD_CHOICES[0],
        )
        self._add_repository_argument(prs_merge)

        releases_parser = subparsers.add_parser("releases", help="Release operations")
        releases_subparsers = releases_parser.add_subparsers(dest="action", required=True)
        releases_list = releases_subparsers.add_parser("list", help="List releases")
        releases_list.add_argument("--limit", type=int, default=20)
        self._add_repository_argument(releases_list)
        releases_create = releases_subparsers.add_parser("create", help="Create release")
        releases_create.add_argument("--tag", required=True)
        releases_create.add_argument("--title", required=True)
        releases_create.add_argument("--notes", default="")
        self._add_repository_argument(releases_create)

    def handle(self, *args, **options):
        """Execute the selected repository operation.

        Parameters:
            *args: Unused positional arguments supplied by Django.
            **options: Parsed CLI options.

        Returns:
            None.

        Raises:
            CommandError: If repository resolution or release management fails.
        """

        repository = self._resolve_repository(self._get_repository_input(options))
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

            if resource == "issues" and action == "show":
                number = int(options["number"])
                issue = self._find_issue(client, repository, number=number)
                activity = client.list_issue_activity(repository, number=number)
                self._write_activity_report(
                    "Issue",
                    number=number,
                    summary=issue,
                    activity=activity,
                )
                return

            if resource == "issues" and action == "create":
                url = client.create_issue(
                    repository,
                    title=str(options["title"]),
                    body=str(options["body"]),
                )
                self.stdout.write(self.style.SUCCESS(f"Issue created: {url}"))
                return

            if resource == "issues" and action == "comment":
                number = int(options["number"])
                client.comment_issue(
                    repository,
                    number=number,
                    body=str(options["body"]),
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Comment added to issue #{number} in {repository.slug}"
                    )
                )
                return

            if resource == "issues" and action == "close":
                number = int(options["number"])
                client.close_issue(repository, number=number)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Issue #{number} closed in {repository.slug}"
                    )
                )
                return

            if resource == "prs" and action == "list":
                rows = client.list_pull_requests(repository, state=str(options.get("state") or "open"))
                for item in rows:
                    draft_label = " draft" if item.get("isDraft") else ""
                    self.stdout.write(
                        f"#{item.get('number')} [{item.get('state')}{draft_label}] {item.get('title')}"
                    )
                self.stdout.write(
                    self.style.SUCCESS(f"Listed {len(rows)} pull requests from {repository.slug}")
                )
                return

            if resource == "prs" and action == "show":
                number = int(options["number"])
                pull_request = self._find_pull_request(client, repository, number=number)
                activity = client.list_pull_request_activity(repository, number=number)
                self._write_activity_report(
                    "Pull request",
                    number=number,
                    summary=pull_request,
                    activity=activity,
                )
                return

            if resource == "prs" and action == "comment":
                number = int(options["number"])
                client.comment_pull_request(
                    repository,
                    number=number,
                    body=str(options["body"]),
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Comment added to pull request #{number} in {repository.slug}"
                    )
                )
                return

            if resource == "prs" and action == "ready":
                number = int(options["number"])
                client.mark_pull_request_ready(repository, number=number)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Pull request #{number} is ready for review in {repository.slug}"
                    )
                )
                return

            if resource == "prs" and action == "merge":
                number = int(options["number"])
                merge_method = str(options["method"])
                client.merge_pull_request(
                    repository,
                    number=number,
                    merge_method=merge_method,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Pull request #{number} merged with {merge_method} in {repository.slug}"
                    )
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

    def _add_repository_argument(self, parser):
        """Attach the optional positional repository argument to a leaf parser.

        Parameters:
            parser: The leaf parser that should accept a repository slug.

        Returns:
            None.
        """

        parser.add_argument(
            "--repo",
            default="",
            help=(
                "Repository slug in owner/name format. Defaults to active package repository "
                f"or {DEFAULT_PACKAGE.repository_url}."
            ),
        )
        parser.add_argument(
            "repository",
            nargs="?",
            help="Optional repository slug in owner/name format.",
        )

    def _get_repository_input(self, options: dict[str, object]) -> str:
        """Resolve the raw repository input from positional and option forms.

        Parameters:
            options: Parsed CLI options.

        Returns:
            The raw repository string to validate.

        Raises:
            CommandError: If positional and option repository inputs conflict.
        """

        positional = str(options.get("repository") or "").strip()
        option = str(options.get("repo") or "").strip()
        if positional and option and positional != option:
            raise CommandError(
                "Repository provided positionally and via --repo must match when both are supplied"
            )
        return positional or option

    def _resolve_repository(self, raw_repo: str) -> RepositoryRef:
        """Validate and normalize a repository reference.

        Parameters:
            raw_repo: Repository slug or URL provided by the user.

        Returns:
            A normalized repository reference.

        Raises:
            CommandError: If the repository input is invalid or cannot be resolved.
        """

        from apps.repos.github import parse_repository_url, resolve_active_repository

        cleaned = raw_repo.strip()
        if cleaned:
            if "://" not in cleaned:
                path_segments = [segment for segment in cleaned.split("/") if segment]
                if len(path_segments) != 2:
                    raise CommandError("Repository must be in owner/name format")
            if "/" not in cleaned:
                raise CommandError("Repository must be in owner/name format")
            try:
                owner, name = parse_repository_url(cleaned)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            return RepositoryRef(owner=owner, name=name)

        try:
            active = resolve_active_repository()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        return RepositoryRef(owner=active.owner, name=active.name)

    @staticmethod
    def _find_issue(
        client: ReleaseManagementClient,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> dict[str, object] | None:
        rows = client.list_issues(repository, state="all")
        return next((row for row in rows if row.get("number") == number), None)

    @staticmethod
    def _find_pull_request(
        client: ReleaseManagementClient,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> dict[str, object] | None:
        rows = client.list_pull_requests(repository, state="all")
        return next((row for row in rows if row.get("number") == number), None)

    def _write_activity_report(
        self,
        label: str,
        *,
        number: int,
        summary: dict[str, object] | None,
        activity: list[dict[str, object]],
    ) -> None:
        state = str((summary or {}).get("state") or "unknown")
        title = str((summary or {}).get("title") or "").strip()
        suffix = ""
        if bool((summary or {}).get("isDraft")):
            suffix = " draft"
        self.stdout.write(f"{label} #{number} [{state}{suffix}] {title}".rstrip())

        url = str((summary or {}).get("url") or "").strip()
        if url:
            self.stdout.write(f"URL: {url}")

        if not activity:
            self.stdout.write("No GitHub comments found.")
            return

        self.stdout.write("Activity:")
        for item in activity:
            author_name = str(item.get("author_name") or "unknown")
            kind_label = str(item.get("kind_label") or "Comment")
            created_at = str(item.get("created_at") or "").strip()
            location = str(item.get("path") or "").strip()
            line = item.get("line")
            header = f"- {kind_label} by {author_name}"
            if created_at:
                header += f" at {created_at}"
            if location:
                if isinstance(line, int):
                    location = f"{location}:{line}"
                header += f" [{location}]"
            self.stdout.write(header)

            reactions = item.get("reactions") or []
            if isinstance(reactions, list) and reactions:
                displays = [
                    str(reaction.get("display") or "").strip()
                    for reaction in reactions
                    if isinstance(reaction, dict)
                ]
                displays = [display for display in displays if display]
                if displays:
                    self.stdout.write(f"  Reactions: {' | '.join(displays)}")

            body = str(item.get("body") or "").strip()
            if body:
                for line_text in body.splitlines():
                    self.stdout.write(f"  {line_text}")
