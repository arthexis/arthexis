from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess

from django.core.management.base import BaseCommand, CommandError

from apps.sites.workgroup_passwords import current_password, password_record_for_date

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")


class Command(BaseCommand):
    help = (
        "Show the current Workgroup play SSH password or apply it to a local "
        "Unix account."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Generate the password for a specific local date, YYYY-MM-DD.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print password metadata as JSON.",
        )
        parser.add_argument(
            "--apply-user",
            metavar="USER",
            help="Set USER's local Unix password to the generated password.",
        )

    def _password_record(self, date_value: str | None):
        if not date_value:
            return current_password()
        try:
            day = dt.date.fromisoformat(date_value)
        except ValueError as exc:
            raise CommandError("--date must be in YYYY-MM-DD format.") from exc
        return password_record_for_date(day)

    def _apply_user_password(self, username: str, password: str) -> None:
        if not _USER_RE.match(username):
            raise CommandError(
                "Invalid Unix username. Use letters, digits, underscore, dash, "
                "and an optional trailing dollar."
            )
        command = (
            ["chpasswd"]
            if hasattr(os, "geteuid") and os.geteuid() == 0
            else ["sudo", "chpasswd"]
        )
        try:
            subprocess.run(
                command,
                input=f"{username}:{password}\n",
                text=True,
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise CommandError(
                f"Password update command not found: {command[0]}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            message = f"Failed to update password for {username}."
            if detail:
                message = f"{message} {detail}"
            raise CommandError(message) from exc

    def handle(self, *args, **options):
        record = self._password_record(options.get("date"))
        apply_user = options.get("apply_user")
        if apply_user:
            self._apply_user_password(apply_user, record.password)
            self.stdout.write(
                f"Updated password for {apply_user} for {record.date.isoformat()}."
            )
            return

        if options.get("json"):
            self.stdout.write(
                json.dumps(
                    {
                        "password": record.password,
                        "date": record.date.isoformat(),
                        "timezone": record.timezone_name,
                        "valid_from": record.valid_from.isoformat(),
                        "valid_until": record.valid_until.isoformat(),
                    },
                    sort_keys=True,
                )
            )
            return

        self.stdout.write(record.password)
