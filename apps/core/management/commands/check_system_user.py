from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.users.system import collect_system_user_issues, ensure_system_user


class Command(BaseCommand):
    """Validate that the system account is available and secured."""

    help = "Verify the system account exists and only allows temporary passwords."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create or repair the system account when issues are detected.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = getattr(User, "SYSTEM_USERNAME", "")
        if not username:
            raise CommandError("The user model does not define a system username.")

        manager = getattr(User, "all_objects", User._default_manager)
        user = manager.filter(username=username).first()
        force = bool(options.get("force"))

        if user is None:
            if not force:
                raise CommandError(
                    f"No account exists for username {username!r}. Use --force to create it."
                )
            ensure_system_user()
            self.stdout.write(self.style.SUCCESS(f"Created system account {username!r}."))
            return

        issues = list(collect_system_user_issues(user))
        if issues and not force:
            buffer = io.StringIO()
            buffer.write(
                f"Issues detected with the {username!r} account. Use --force to repair it.\n"
            )
            for issue in issues:
                buffer.write(f" - {issue}\n")
            raise CommandError(buffer.getvalue().rstrip())

        if force:
            _user, updated = ensure_system_user(record_updates=True)
            if updated:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Repaired system account {username!r}: {', '.join(sorted(updated))}."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"System account {username!r} is already healthy.")
                )
            return

        self.stdout.write(self.style.SUCCESS(f"System account {username!r} is healthy."))

