"""Manage admin-site runtime settings stored in ``arthexis.env``."""

from __future__ import annotations

from collections import OrderedDict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from filelock import FileLock, Timeout

from apps.core.management.commands.env import env_path, read_env, write_env
from config.admin_urls import normalize_admin_url_path


class Command(BaseCommand):
    """Configure Django admin mount and branding settings."""

    help = (
        "Manage admin-site settings such as mount path and admin-site branding. "
        "Values are persisted to arthexis.env."
    )

    SETTABLE_FIELDS = {
        "path": "ADMIN_URL_PATH",
        "header": "ADMIN_SITE_HEADER",
        "title": "ADMIN_SITE_TITLE",
        "index_title": "ADMIN_INDEX_TITLE",
    }

    def add_arguments(self, parser):
        """Register command sub-actions and options."""

        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        subparsers.add_parser(
            "show",
            help="Show active admin-site settings.",
        )

        set_parser = subparsers.add_parser(
            "set",
            help="Persist one or more admin-site settings into arthexis.env.",
        )
        set_parser.add_argument("--path", help="Admin URL path (example: control/).")
        set_parser.add_argument("--header", help="Admin site header text.")
        set_parser.add_argument("--title", help="Admin site title text.")
        set_parser.add_argument("--index-title", dest="index_title", help="Admin index title text.")

        reset_parser = subparsers.add_parser(
            "reset",
            help="Remove selected admin-site settings from arthexis.env.",
        )
        reset_parser.add_argument("--path", action="store_true", help="Remove ADMIN_URL_PATH.")
        reset_parser.add_argument("--header", action="store_true", help="Remove ADMIN_SITE_HEADER.")
        reset_parser.add_argument("--title", action="store_true", help="Remove ADMIN_SITE_TITLE.")
        reset_parser.add_argument(
            "--index-title",
            dest="index_title",
            action="store_true",
            help="Remove ADMIN_INDEX_TITLE.",
        )
        reset_parser.add_argument(
            "--all",
            action="store_true",
            help="Remove all managed admin-site keys.",
        )

    def handle(self, *args, **options):
        """Dispatch sub-action handlers."""

        action = options["action"]
        if action == "show":
            self._handle_show()
        elif action == "set":
            self._handle_set(options)
        elif action == "reset":
            self._handle_reset(options)
        else:
            raise CommandError(f"Unsupported admin action: {action}")

    def _handle_show(self) -> None:
        """Print currently active admin-site values."""

        self.stdout.write(f"ADMIN_URL_PATH={settings.ADMIN_URL_PATH}")
        self.stdout.write(f"ADMIN_SITE_HEADER={settings.ADMIN_SITE_HEADER}")
        self.stdout.write(f"ADMIN_SITE_TITLE={settings.ADMIN_SITE_TITLE}")
        self.stdout.write(f"ADMIN_INDEX_TITLE={settings.ADMIN_INDEX_TITLE}")

    def _handle_set(self, options: dict[str, object]) -> None:
        """Persist provided values after validating user input."""

        updates: dict[str, str] = {}

        raw_path = options.get("path")
        if raw_path is not None:
            try:
                updates["ADMIN_URL_PATH"] = normalize_admin_url_path(str(raw_path))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        for option_name, env_key in self.SETTABLE_FIELDS.items():
            if option_name == "path":
                continue
            raw_value = options.get(option_name)
            if raw_value is not None:
                value = str(raw_value).strip()
                if not value:
                    raise CommandError(f"{env_key} cannot be blank.")
                updates[env_key] = value

        if not updates:
            raise CommandError("Provide at least one value to set.")

        self._persist_updates(updates)
        self.stdout.write(self.style.SUCCESS("Admin settings updated in arthexis.env."))

    def _handle_reset(self, options: dict[str, object]) -> None:
        """Delete selected settings from ``arthexis.env``."""

        selected_keys: set[str] = set()
        if bool(options.get("all")):
            selected_keys.update(self.SETTABLE_FIELDS.values())

        for option_name, env_key in self.SETTABLE_FIELDS.items():
            if bool(options.get(option_name)):
                selected_keys.add(env_key)

        if not selected_keys:
            raise CommandError("Choose at least one setting to reset or pass --all.")

        self._persist_deletes(selected_keys)
        self.stdout.write(self.style.SUCCESS("Admin settings removed from arthexis.env."))

    def _persist_updates(self, updates: dict[str, str]) -> None:
        """Write updated keys to ``arthexis.env`` under a file lock."""

        path = env_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")

        try:
            with FileLock(lock_path, timeout=5):
                values: OrderedDict[str, str] = read_env(path)
                for key, value in updates.items():
                    values[key] = value
                write_env(path, values)
        except Timeout as exc:
            raise CommandError("Could not acquire lock to modify arthexis.env.") from exc

    def _persist_deletes(self, keys: set[str]) -> None:
        """Remove keys from ``arthexis.env`` under a file lock."""

        path = env_path()
        if not path.exists():
            return

        lock_path = path.with_suffix(path.suffix + ".lock")
        try:
            with FileLock(lock_path, timeout=5):
                values: OrderedDict[str, str] = read_env(path)
                for key in sorted(keys):
                    values.pop(key, None)
                write_env(path, values)
        except Timeout as exc:
            raise CommandError("Could not acquire lock to modify arthexis.env.") from exc
