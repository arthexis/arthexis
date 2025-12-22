from __future__ import annotations

import io
from typing import Iterable

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Validate that the default admin account is available."""

    help = "Verify the default admin account exists and is usable."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create or repair the default admin account when issues are detected.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = getattr(User, "ADMIN_USERNAME", "admin")
        if not username:
            raise CommandError("The user model does not define an admin username.")

        manager = getattr(User, "all_objects", User._default_manager)
        user = manager.filter(username=username).first()
        force = bool(options.get("force"))

        if user is None:
            if not force:
                raise CommandError(
                    f"No account exists for username {username!r}. Use --force to create it."
                )
            user = self._create_admin(User, username)
            self.stdout.write(
                self.style.SUCCESS(f"Created default admin account {username!r}.")
            )
            return

        issues = list(self._collect_issues(user))
        if issues and not force:
            buffer = io.StringIO()
            buffer.write(
                f"Issues detected with the {username!r} account. Use --force to repair it.\n"
            )
            for issue in issues:
                buffer.write(f" - {issue}\n")
            raise CommandError(buffer.getvalue().rstrip())

        if force:
            updated = self._repair_admin(user)
            if updated:
                user.save(update_fields=sorted(updated))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Repaired default admin account {username!r}: {', '.join(sorted(updated))}."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"Default admin account {username!r} is already healthy.")
                )
            return

        self.stdout.write(self.style.SUCCESS(f"Default admin account {username!r} is healthy."))

    def _collect_issues(self, user) -> Iterable[str]:
        if getattr(user, "is_deleted", False):
            yield "account is marked as deleted"
        if not getattr(user, "is_active", True):
            yield "account is inactive"
        if not getattr(user, "is_staff", True):
            yield "account is not marked as staff"
        if not getattr(user, "is_superuser", True):
            yield "account is not a superuser"
        if not user.password:
            yield "account does not have a password"
        elif not user.has_usable_password():
            yield "account password is unusable"

    def _repair_admin(self, user) -> set[str]:
        updated: set[str] = set()
        if getattr(user, "is_deleted", False):
            user.is_deleted = False
            updated.add("is_deleted")
        if not getattr(user, "is_active", True):
            user.is_active = True
            updated.add("is_active")
        if not getattr(user, "is_staff", True):
            user.is_staff = True
            updated.add("is_staff")
        if not getattr(user, "is_superuser", True):
            user.is_superuser = True
            updated.add("is_superuser")
        delegate = self._resolve_system_delegate(user)
        if delegate is not None and user.operate_as_id in {None, user.pk}:
            user.operate_as = delegate
            updated.add("operate_as")
        if not user.password or not user.has_usable_password() or not user.check_password(
            "admin"
        ):
            user.set_password("admin")
            updated.add("password")
        return updated

    def _create_admin(self, User, username):
        user = User.all_objects.create(
            username=username,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        user.set_password("admin")
        delegate = self._resolve_system_delegate(user)
        if delegate is not None:
            user.operate_as = delegate
        user.save()
        from apps.locals.models import ensure_admin_favorites

        ensure_admin_favorites(user)
        return user

    def _resolve_system_delegate(self, user):
        User = type(user)
        system_username = getattr(User, "SYSTEM_USERNAME", "")
        if not system_username:
            return None
        manager = getattr(User, "all_objects", User._default_manager)
        delegate = manager.filter(username=system_username).exclude(pk=user.pk).first()
        if delegate is None:
            return None
        if not getattr(delegate, "is_staff", True) or not getattr(delegate, "is_superuser", True):
            return None
        return delegate
