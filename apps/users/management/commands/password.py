from __future__ import annotations

import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.db.utils import OperationalError
from django.utils import timezone

from apps.groups.security import ensure_default_staff_groups
from apps.users import temp_passwords
from apps.users.management.commands.utils import coerce_option_list


class Command(BaseCommand):
    """Manage account passwords and password-related flags for a single user."""

    help = (
        "Generate or set temporary/permanent passwords, clear passwords, and "
        "toggle forced password change using username, email, or user id."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            nargs="?",
            help="Username, email address, or user id identifying the user.",
        )
        parser.add_argument(
            "--lookup",
            choices=("auto", "username", "email", "id"),
            default="auto",
            help="Lookup strategy for the provided identifier.",
        )
        parser.add_argument(
            "--temporary",
            action="store_true",
            help="Store the password as a temporary password instead of permanent.",
        )
        parser.add_argument(
            "--expires-in",
            type=int,
            default=int(temp_passwords.DEFAULT_EXPIRATION.total_seconds()),
            help=(
                "Number of seconds before a temporary password expires. "
                "Defaults to 3600 (1 hour)."
            ),
        )
        parser.add_argument(
            "--allow-change",
            action="store_true",
            help=(
                "Allow temporary passwords to satisfy old-password checks when "
                "changing permanent passwords."
            ),
        )
        parser.add_argument(
            "--password",
            dest="raw_password",
            help="Explicit password to set or store.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete/disable the account password and clear temporary credentials.",
        )
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create the user when it does not exist.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update the user when applying staff/superuser flags.",
        )
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Grant staff privileges when creating or updating a user.",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Grant superuser privileges when creating or updating a user.",
        )
        parser.add_argument(
            "--force-change",
            dest="force_change",
            action="store_true",
            help="Force the user to change password on next visit.",
        )
        parser.add_argument(
            "--no-force-change",
            dest="force_change",
            action="store_false",
            help="Do not force a password change on next visit.",
        )
        parser.add_argument(
            "--group",
            action="append",
            default=[],
            dest="groups",
            help="Assign the user to the provided group. May be passed multiple times.",
        )
        parser.add_argument(
            "--access-point-user",
            action="store_true",
            help=(
                "Configure a non-staff account for local-network access point login "
                "without password verification."
            ),
        )
        parser.set_defaults(force_change=None)

    def handle(self, *args, **options):
        identifier = options.get("identifier")
        create_user = bool(options.get("create"))
        update_user = bool(options.get("update"))
        staff = bool(options.get("staff"))
        superuser = bool(options.get("superuser"))
        delete_password = bool(options.get("delete"))
        temporary = bool(options.get("temporary"))
        allow_change = bool(options.get("allow_change"))
        raw_password = options.get("raw_password")
        force_change = options.get("force_change")
        groups = coerce_option_list(options.get("groups"))
        access_point_user = bool(options.get("access_point_user"))

        if delete_password and raw_password:
            raise CommandError("--password cannot be used together with --delete.")
        if temporary and delete_password:
            raise CommandError("--temporary cannot be used together with --delete.")
        if (staff or superuser) and not (create_user or update_user):
            raise CommandError(
                "--staff and --superuser can only be used with --create or --update."
            )
        if access_point_user and (staff or superuser):
            raise CommandError("--access-point-user cannot be combined with --staff or --superuser.")
        if access_point_user and temporary:
            raise CommandError("--access-point-user cannot be combined with --temporary.")
        if access_point_user and delete_password:
            raise CommandError("--access-point-user cannot be combined with --delete.")
        if access_point_user and raw_password:
            raise CommandError("--access-point-user cannot be combined with --password.")

        if identifier is None:
            if delete_password:
                raise CommandError("identifier is required when using --delete.")
            if groups:
                raise CommandError("identifier is required when using --group.")
            if access_point_user:
                raise CommandError("identifier is required when using --access-point-user.")
            generated_password = raw_password or temp_passwords.generate_password()
            self.stdout.write(f"Generated password: {generated_password}")
            self.stdout.write(self.style.SUCCESS("Password generated."))
            return

        User = get_user_model()
        manager = getattr(User, "all_objects", User._default_manager)

        users = self._resolve_users(manager, identifier, lookup=options["lookup"])
        created = False
        if not users:
            if not create_user:
                raise CommandError(
                    f"No user found for identifier {identifier!r}. Use --create to add one."
                )
            users = [self._create_user(manager, identifier, staff=staff, superuser=superuser)]
            created = True

        if len(users) > 1:
            usernames = ", ".join(sorted({user.username for user in users}))
            raise CommandError(
                "Multiple users share this email address. Provide a specific identifier. "
                f"Matches: {usernames}"
            )

        user = users[0]
        if (
            update_user
            or access_point_user
            or (create_user and not created and (staff or superuser or access_point_user))
        ):
            self._update_user(
                user,
                staff=staff,
                superuser=superuser,
                access_point_user=access_point_user,
            )
        if access_point_user:
            self._configure_access_point_user(user)
            self._harden_access_point_membership(user, groups)
            self.stdout.write(self.style.SUCCESS(f"Configured {user.username} as a local access point user."))
            return

        if groups:
            self._assign_groups(user, groups)
        ensure_default_staff_groups(user, explicit_group_names=groups)

        if delete_password:
            self._delete_password(user)
            self.stdout.write(self.style.SUCCESS(f"Password deleted for {user.username}."))
            return

        password = raw_password or temp_passwords.generate_password()
        default_force_change = not temporary
        effective_force_change = default_force_change if force_change is None else bool(force_change)

        if temporary:
            expires_in = int(options["expires_in"])
            if expires_in <= 0:
                raise CommandError("Expiration must be a positive number of seconds.")
            expires_at = timezone.now() + timedelta(seconds=expires_in)
            self._reactivate_user(user)
            entry = temp_passwords.store_temp_password(
                user.username,
                password,
                expires_at,
                allow_change=allow_change,
            )
            self._set_force_password_change(user, effective_force_change)
            self.stdout.write(
                self._render_temporary_output(user.username, password, entry.expires_at, allow_change)
            )
            self.stdout.write(self.style.SUCCESS("Temporary password created."))
            return

        user.set_password(password)
        user.save(update_fields=["password"])
        self._set_force_password_change(user, effective_force_change)
        self.stdout.write(self._render_permanent_output(user.username, password, effective_force_change))
        self.stdout.write(self.style.SUCCESS("Permanent password updated."))

    def _resolve_users(self, manager, identifier, *, lookup: str):
        try:
            queryset = self._build_queryset(manager, identifier, lookup=lookup)
            return list(queryset.order_by("username"))
        except OperationalError as exc:
            message = str(exc).lower()
            missing_column_signatures = (
                "no such column",
                "undefined column",
                "unknown column",
                "column does not exist",
            )
            if any(signature in message for signature in missing_column_signatures):
                raise CommandError(
                    "The database schema is out of date. Run migrations before managing "
                    "passwords."
                ) from exc
            raise

    def _build_queryset(self, manager, identifier, *, lookup: str):
        if lookup == "username":
            return manager.filter(username__iexact=identifier)
        if lookup == "email":
            return manager.filter(email__iexact=identifier)
        if lookup == "id":
            try:
                return manager.filter(pk=int(identifier))
            except (TypeError, ValueError) as exc:
                raise CommandError("--lookup id requires an integer identifier.") from exc

        filters = Q(username__iexact=identifier) | Q(email__iexact=identifier)
        try:
            filters |= Q(pk=int(identifier))
        except (TypeError, ValueError):
            pass
        return manager.filter(filters)

    def _create_user(self, manager, identifier, *, staff: bool = False, superuser: bool = False):
        kwargs = {"username": str(identifier)}
        identifier_text = str(identifier)
        if "@" in identifier_text and not identifier_text.startswith("@"):
            kwargs["email"] = identifier_text

        user = manager.create_user(**kwargs)
        user.set_unusable_password()
        fields = {"password"}
        if staff:
            user.is_staff = True
            fields.add("is_staff")
        if superuser:
            user.is_superuser = True
            user.is_staff = True
            fields.update(["is_superuser", "is_staff"])
        user.save(update_fields=list(fields))
        return user

    def _update_user(
        self,
        user,
        *,
        staff: bool = False,
        superuser: bool = False,
        access_point_user: bool = False,
    ) -> None:
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
        if access_point_user:
            if user.is_staff:
                user.is_staff = False
                if "is_staff" not in fields:
                    fields.append("is_staff")
            if user.is_superuser:
                user.is_superuser = False
                if "is_superuser" not in fields:
                    fields.append("is_superuser")
        if fields:
            user.save(update_fields=fields)

    def _configure_access_point_user(self, user) -> None:
        fields = []
        if not getattr(user, "allow_local_network_passwordless_login", False):
            user.allow_local_network_passwordless_login = True
            fields.append("allow_local_network_passwordless_login")
        if user.force_password_change:
            user.force_password_change = False
            fields.append("force_password_change")
        if user.has_usable_password():
            user.set_unusable_password()
            fields.append("password")
        if getattr(user, "temporary_expires_at", None) is not None:
            user.temporary_expires_at = None
            fields.append("temporary_expires_at")
        temp_passwords.discard_temp_password(user.username)
        if fields:
            user.save(update_fields=fields)

    def _harden_access_point_membership(self, user, groups: list[str]) -> None:
        resolved_groups = self._resolve_groups(groups) if groups else []
        user.groups.clear()
        if resolved_groups:
            user.groups.add(*resolved_groups)

    def _delete_password(self, user) -> None:
        user.set_unusable_password()
        user.force_password_change = False
        temp_passwords.discard_temp_password(user.username)

        fields = ["password", "force_password_change"]
        if getattr(user, "temporary_expires_at", None) is not None:
            user.temporary_expires_at = None
            fields.append("temporary_expires_at")

        user.save(update_fields=fields)

    def _assign_groups(self, user, groups: list[str]) -> None:
        """Assign a user to one or more existing auth groups."""

        user.groups.add(*self._resolve_groups(groups))

    def _resolve_groups(self, groups: list[str]) -> list[Group]:
        """Resolve requested group names and raise when any are unknown."""

        existing_groups = {
            group.name: group for group in Group.objects.filter(name__in=groups).order_by("name")
        }
        missing_groups = sorted(set(groups) - set(existing_groups))
        if missing_groups:
            missing_names = ", ".join(missing_groups)
            raise CommandError(f"Unknown groups: {missing_names}")
        return [existing_groups[name] for name in groups]

    def _set_force_password_change(self, user, force_change: bool) -> None:
        if user.force_password_change == force_change:
            return
        user.force_password_change = force_change
        user.save(update_fields=["force_password_change"])

    def _reactivate_user(self, user) -> None:
        """Clear expired temporary credentials so fresh passwords work."""

        expiration = getattr(user, "temporary_expires_at", None)
        if expiration is None or expiration > timezone.now():
            return

        user.temporary_expires_at = None
        user.save(update_fields=["temporary_expires_at"])

    def _render_temporary_output(
        self,
        username: str,
        password: str,
        expires_at,
        allow_change: bool,
    ) -> str:
        buffer = io.StringIO()
        buffer.write(f"Temporary password for {username}: {password}\n")
        buffer.write(f"Expires at: {expires_at.isoformat()}\n")
        if allow_change:
            buffer.write(
                "This password can be used to satisfy the old password "
                "requirement when changing the account password.\n"
            )
        return buffer.getvalue()

    def _render_permanent_output(
        self,
        username: str,
        password: str,
        force_change: bool,
    ) -> str:
        buffer = io.StringIO()
        buffer.write(f"Permanent password for {username}: {password}\n")
        if force_change:
            buffer.write("User must change password on next visit.\n")
        else:
            buffer.write("Forced password change is disabled.\n")
        return buffer.getvalue()
