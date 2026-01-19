from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Create or update the docs admin user for documentation captures."""

    help = "Create or update the docs admin user with a known password."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="docs",
            help="Username for the docs admin account.",
        )
        parser.add_argument(
            "--email",
            default="docs@example.com",
            help="Email address for the docs admin account.",
        )
        parser.add_argument(
            "--password",
            default="docs",
            help="Password to set for the docs admin account.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm that you want to create/update this account.",
        )

    def handle(self, *args, **options):
        if not options.get("confirm"):
            raise CommandError("Pass --confirm to create or update the docs admin user.")

        username = options["username"]
        email = options["email"]
        password = options["password"]

        User = get_user_model()
        manager = getattr(User, "all_objects", User._default_manager)
        user, created = manager.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )

        updated_fields = []
        if user.email != email:
            user.email = email
            updated_fields.append("email")
        if not user.is_staff:
            user.is_staff = True
            updated_fields.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            updated_fields.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            updated_fields.append("is_active")

        user.set_password(password)
        updated_fields.append("password")
        user.save(update_fields=updated_fields)

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} docs admin user '{username}'."))
