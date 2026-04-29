"""Manage GitHub credentials for suite operations."""

from __future__ import annotations

from argparse import ArgumentParser
from getpass import getpass

from filelock import FileLock

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.management.commands.env import env_path, read_env, write_env
from apps.repos.models import GitHubToken
from apps.repos.services import github as github_service


class Command(BaseCommand):
    """Manage GitHub tokens for users or global environment usage."""

    help = "Manage GitHub tokens. Use `github set-token --user <username>` or `github set-token --global`."

    def add_arguments(self, parser: ArgumentParser) -> None:
        subparsers = parser.add_subparsers(dest="action", required=True)
        set_token = subparsers.add_parser("set-token", help="Validate and store a GitHub token.")
        target_group = set_token.add_mutually_exclusive_group(required=True)
        target_group.add_argument(
            "--global",
            action="store_true",
            dest="global_target",
            help="Store token as GITHUB_TOKEN in arthexis.env.",
        )
        target_group.add_argument(
            "--user",
            metavar="USERNAME",
            help="Store token on the specified user's GitHubToken record.",
        )

    def handle(self, *args, **options):
        action = options.get("action")
        if action != "set-token":
            raise CommandError(f"Unsupported action: {action}")

        try:
            token = getpass("GitHub token: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise CommandError("Token entry aborted.") from exc
        if not token:
            raise CommandError("No token entered.")

        is_valid, message, github_login = github_service.validate_token(token)
        if not is_valid:
            raise CommandError(f"Token validation failed: {message}")

        self.stdout.write(self.style.SUCCESS(message))
        target_label = "global environment" if options.get("global_target") else f"user {options.get('user')}"
        try:
            confirm = input(f"Store token for {target_label}? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt) as exc:
            raise CommandError("Confirmation aborted.") from exc
        if confirm not in {"y", "yes"}:
            self.stdout.write("Aborted without saving.")
            return

        if options.get("global_target"):
            self._store_global_token(token)
            self.stdout.write(self.style.SUCCESS("Stored token as GITHUB_TOKEN in arthexis.env."))
            return

        username = str(options.get("user") or "").strip()
        self._store_user_token(username=username, token=token, github_login=github_login)
        self.stdout.write(self.style.SUCCESS(f"Stored token for user '{username}'."))

    def _store_global_token(self, token: str) -> None:
        path = env_path()
        lock_path = path.with_suffix(path.suffix + ".lock")
        with FileLock(lock_path, timeout=5):
            values = read_env(path)
            values["GITHUB_TOKEN"] = token
            write_env(path, values)

    def _store_user_token(self, *, username: str, token: str, github_login: str) -> None:
        if not username:
            raise CommandError("--user requires a username.")

        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User not found: {username}") from exc

        label = github_login or "CLI token"
        GitHubToken.objects.update_or_create(
            user=user,
            defaults={"label": label, "token": token},
        )
