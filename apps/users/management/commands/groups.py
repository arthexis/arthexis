from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """List groups and manage group memberships."""

    help = "List groups and optionally add/remove users to/from a group."

    def add_arguments(self, parser):
        parser.add_argument(
            "group",
            nargs="?",
            help="Group name used when changing membership.",
        )
        parser.add_argument(
            "--add",
            action="append",
            default=[],
            dest="add_usernames",
            help="Username to add to the target group. May be passed multiple times.",
        )
        parser.add_argument(
            "--remove",
            action="append",
            default=[],
            dest="remove_usernames",
            help="Username to remove from the target group. May be passed multiple times.",
        )

    def handle(self, *args, **options):
        group_name = options.get("group")
        add_usernames = self._normalize_usernames(self._coerce_option_list(options.get("add_usernames")))
        remove_usernames = self._normalize_usernames(
            self._coerce_option_list(options.get("remove_usernames"))
        )

        if add_usernames or remove_usernames:
            if not group_name:
                raise CommandError("A group name is required when using --add/--remove.")
            self._manage_members(group_name, add_usernames, remove_usernames)
            return

        self._list_groups()

    def _normalize_usernames(self, usernames: list[str]) -> list[str]:
        """Clean and deduplicate usernames while preserving order."""

        seen = set()
        normalized: list[str] = []
        for username in usernames:
            candidate = username.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def _coerce_option_list(self, value) -> list[str]:
        """Normalize argparse/list-like option values into clean string lists."""

        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    def _manage_members(self, group_name: str, add_usernames: list[str], remove_usernames: list[str]) -> None:
        """Add and remove users for a single group in one command execution."""

        group = Group.objects.filter(name=group_name).first()
        if group is None:
            raise CommandError(f"Group '{group_name}' does not exist.")

        user_model = get_user_model()
        requested_usernames = sorted(set(add_usernames + remove_usernames))
        users_by_username = {
            user.username: user
            for user in user_model.objects.filter(username__in=requested_usernames).order_by("username")
        }
        missing_usernames = sorted(set(requested_usernames) - set(users_by_username))
        if missing_usernames:
            missing = ", ".join(missing_usernames)
            raise CommandError(f"Unknown users: {missing}")

        if add_usernames:
            group.user_set.add(*[users_by_username[username] for username in add_usernames])
        if remove_usernames:
            group.user_set.remove(*[users_by_username[username] for username in remove_usernames])

        self.stdout.write(self.style.SUCCESS(f"Updated membership for group '{group.name}'."))

    def _list_groups(self) -> None:
        """Render all groups with current member counts and usernames."""

        groups = Group.objects.order_by("name")
        if not groups.exists():
            self.stdout.write("No groups found.")
            return

        for group in groups:
            members = list(group.user_set.order_by("username").values_list("username", flat=True))
            members_text = ", ".join(members) if members else "(no members)"
            self.stdout.write(f"{group.name} ({len(members)}): {members_text}")
