from __future__ import annotations

import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError
from django.utils import timezone

from apps.users import temp_passwords


class Command(BaseCommand):
    """Create a temporary password for the requested user."""

    help = "Generate a temporary password for a user by username or email."

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            help="Username or email address identifying the user.",
        )
        parser.add_argument(
            "--expires-in",
            type=int,
            default=int(temp_passwords.DEFAULT_EXPIRATION.total_seconds()),
            help=(
                "Number of seconds before the temporary password expires. "
                "Defaults to 3600 (1 hour)."
            ),
        )
        parser.add_argument(
            "--allow-change",
            action="store_true",
            help=(
                "Allow the generated temporary password to be used as the old "
                "password when changing the permanent password."
            ),
        )
        parser.add_argument(
            "--create",
            action="store_true",
            help=(
                "Create the user if it does not exist. The account will be created "
                "with only the generated temporary password."
            ),
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help=(
                "Update an existing user when generating the temporary password. "
                "Allows adjusting permissions such as --staff or --superuser."
            ),
        )
        parser.add_argument(
            "--staff",
            action="store_true",
            help=(
                "Grant staff privileges to the user when creating or updating the "
                "account."
            ),
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help=(
                "Grant superuser privileges to the user when creating or updating "
                "the account."
            ),
        )

    def handle(self, *args, **options):
        identifier = options["identifier"]
        expires_in = int(options["expires_in"])
        if expires_in <= 0:
            raise CommandError("Expiration must be a positive number of seconds.")
        allow_change = bool(options.get("allow_change"))
        create_user = bool(options.get("create"))
        update_user = bool(options.get("update"))
        staff = bool(options.get("staff"))
        superuser = bool(options.get("superuser"))

        if (staff or superuser) and not (create_user or update_user):
            raise CommandError(
                "--staff and --superuser can only be used with --create or --update."
            )

        User = get_user_model()
        manager = getattr(User, "all_objects", User._default_manager)

        users = self._resolve_users(manager, identifier)
        created = False
        if not users:
            if not create_user:
                raise CommandError(f"No user found for identifier {identifier!r}.")
            users = [self._create_user(manager, identifier, staff=staff, superuser=superuser)]
            created = True
        if len(users) > 1:
            usernames = ", ".join(sorted({user.username for user in users}))
            raise CommandError(
                "Multiple users share this email address. Provide the username "
                f"instead. Matches: {usernames}"
            )

        user = users[0]
        if update_user or (create_user and not created and (staff or superuser)):
            self._update_user(user, staff=staff, superuser=superuser)
        password = temp_passwords.generate_password()
        expires_at = timezone.now() + timedelta(seconds=expires_in)
        self._reactivate_user(user)
        entry = temp_passwords.store_temp_password(
            user.username,
            password,
            expires_at,
            allow_change=allow_change,
        )

        buffer = io.StringIO()
        buffer.write(f"Temporary password for {user.username}: {password}\n")
        buffer.write(f"Expires at: {entry.expires_at.isoformat()}\n")
        if not user.is_active:
            buffer.write("The account will be activated on first use.\n")
        if allow_change:
            buffer.write(
                "This password can be used to satisfy the old password "
                "requirement when changing the account password.\n"
            )
        self.stdout.write(buffer.getvalue())
        self.stdout.write(self.style.SUCCESS("Temporary password created."))

    def _resolve_users(self, manager, identifier):
        if "@" in identifier and not identifier.startswith("@"):
            queryset = manager.filter(email__iexact=identifier)
        else:
            queryset = manager.filter(username__iexact=identifier)
            if not queryset.exists():
                queryset = manager.filter(email__iexact=identifier)
        try:
            return list(queryset.order_by("username"))
        except OperationalError as exc:
            if "require_2fa" in str(exc):
                raise CommandError(
                    "The database schema is out of date. Run migrations to add the "
                    "`require_2fa` column before generating temporary passwords."
                ) from exc
            raise

    def _create_user(self, manager, identifier, *, staff: bool = False, superuser: bool = False):
        kwargs = {"username": identifier}
        if "@" in identifier and not identifier.startswith("@"):
            kwargs["email"] = identifier

        user = manager.create_user(**kwargs)
        user.set_unusable_password()
        fields = ["password"]
        if staff:
            user.is_staff = True
            fields.append("is_staff")
        if superuser:
            user.is_superuser = True
            user.is_staff = True
            fields.extend(["is_superuser", "is_staff"])
        user.save(update_fields=fields)
        return user

    def _update_user(self, user, *, staff: bool = False, superuser: bool = False) -> None:
        fields = []
        if staff and not user.is_staff:
            user.is_staff = True
            fields.append("is_staff")
        if superuser and not user.is_superuser:
            user.is_superuser = True
            fields.append("is_superuser")
        if superuser and not user.is_staff:
            user.is_staff = True
            if "is_staff" not in fields:
                fields.append("is_staff")
        if fields:
            user.save(update_fields=fields)

    def _reactivate_user(self, user) -> None:
        """Clear expired temporary credentials so fresh passwords work."""

        expiration = getattr(user, "temporary_expires_at", None)
        if expiration is None or expiration > timezone.now():
            return

        user.temporary_expires_at = None
        user.save(update_fields=["temporary_expires_at"])

