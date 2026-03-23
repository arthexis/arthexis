"""Django command for inspecting and querying Odoo integrations."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.evergo.models import EvergoUser
from apps.odoo.models import OdooDeployment, OdooEmployee, OdooQuery
from apps.odoo.sync_features import (
    ODOO_SYNC_EVERGO_USERS_PARAMETER_KEY,
    is_odoo_sync_integration_enabled,
)


class Command(BaseCommand):
    """Report Odoo integration state and run ad-hoc Odoo RPC queries."""

    help = (
        "Check Odoo integration health with no arguments, or run an Odoo RPC call "
        "using --model/--method with JSON --params and --kwargs."
    )

    def add_arguments(self, parser) -> None:
        """Define optional RPC arguments for ad-hoc Odoo queries."""

        parser.add_argument(
            "--profile-id",
            type=int,
            help="Specific OdooEmployee profile ID to use for RPC checks.",
        )
        parser.add_argument(
            "--model", help="Odoo model name (for example: sale.order)."
        )
        parser.add_argument(
            "--method", help="Odoo RPC method (for example: search_read)."
        )
        parser.add_argument(
            "--params",
            default="[]",
            help='JSON list passed as *args to execute_kw. Example: \'[[["state","=","sale"]]]\'',
        )
        parser.add_argument(
            "--kwargs",
            default="{}",
            help='JSON object passed as **kwargs to execute_kw. Example: \'{"fields":["name"],"limit":5}\'',
        )
        parser.add_argument(
            "--sync-evergo-users",
            action="store_true",
            help=(
                "Create missing Odoo users from local Evergo users and mirror them as "
                "local Odoo employee profiles."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Run status mode by default, or RPC mode when model/method are provided."""

        if options.get("sync_evergo_users"):
            self._handle_sync_evergo_users_mode(options)
            return

        model_name = options.get("model")
        method_name = options.get("method")

        if model_name or method_name:
            self._handle_rpc_mode(options)
            return

        self._handle_status_mode()

    def _handle_status_mode(self) -> None:
        """Print a compact overview of Odoo integration resources."""

        profile_stats = OdooEmployee.objects.aggregate(
            total=Count("pk"),
            verified=Count("pk", filter=Q(verified_on__isnull=False)),
        )
        total_profiles = profile_stats["total"]
        verified_profiles = profile_stats["verified"]

        deployment_count = OdooDeployment.objects.count()
        latest_discovered = OdooDeployment.objects.aggregate(
            latest=Max("last_discovered")
        )["latest"]

        query_stats = OdooQuery.objects.aggregate(
            total=Count("pk"),
            public=Count("pk", filter=Q(enable_public_view=True)),
        )
        query_count = query_stats["total"]
        public_query_count = query_stats["public"]

        last_seen = "never"
        if latest_discovered is not None:
            last_seen = timezone.localtime(latest_discovered).isoformat()

        self.stdout.write(self.style.MIGRATE_HEADING("Odoo Integration Status"))
        self.stdout.write(
            f"Profiles: total={total_profiles}, verified={verified_profiles}"
        )
        self.stdout.write(
            f"Deployments: total={deployment_count}, last_discovered={last_seen}"
        )
        self.stdout.write(
            f"Saved queries: total={query_count}, public={public_query_count}"
        )

        if verified_profiles == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No verified Odoo employee profiles found; RPC checks will fail until credentials are verified."
                )
            )

    def _handle_rpc_mode(self, options: dict[str, Any]) -> None:
        """Run a user-defined Odoo RPC query and print JSON output."""

        model_name = options.get("model")
        method_name = options.get("method")
        if not model_name or not method_name:
            raise CommandError("Both --model and --method are required for RPC mode.")

        params = self._parse_json_list(
            options.get("params", "[]"), argument_name="--params"
        )
        kwargs = self._parse_json_dict(
            options.get("kwargs", "{}"), argument_name="--kwargs"
        )
        profile = self._resolve_profile(options)

        if not profile.is_verified or profile.odoo_uid is None:
            raise CommandError(
                f"Odoo profile id={profile.pk} is not verified. Verify credentials before RPC execution."
            )

        try:
            result = profile.execute(model_name, method_name, *params, **kwargs)
        except Exception as exc:
            raise CommandError(
                f"Odoo RPC call failed for profile id={profile.pk}: {type(exc).__name__}: {exc}"
            ) from exc

        payload = {
            "profile_id": profile.pk,
            "model": model_name,
            "method": method_name,
            "params": params,
            "kwargs": kwargs,
            "result": result,
        }
        self.stdout.write(json.dumps(payload, default=str, indent=2, sort_keys=True))

    def _handle_sync_evergo_users_mode(self, options: dict[str, Any]) -> None:
        """Create missing Odoo users for discovered Evergo users."""

        if not is_odoo_sync_integration_enabled(
            ODOO_SYNC_EVERGO_USERS_PARAMETER_KEY, default=False
        ):
            raise CommandError(
                "Odoo Evergo user sync integration is disabled by suite feature toggles."
            )

        profile_id = options.get("profile_id")
        if profile_id is None:
            raise CommandError(
                "--profile-id is required for --sync-evergo-users write operations."
            )

        profile = self._resolve_profile(options)
        if not profile.is_verified or profile.odoo_uid is None:
            raise CommandError(
                f"Odoo profile id={profile.pk} is not verified. Verify credentials before syncing Evergo users."
            )

        created = 0
        skipped = 0
        errors = 0

        for evergo_user in EvergoUser.objects.order_by("pk").iterator():
            email = (
                str(evergo_user.email or evergo_user.evergo_email or "").strip().lower()
            )
            if not email:
                skipped += 1
                continue
            if not evergo_user.user_id and not evergo_user.group_id:
                skipped += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipping Evergo user id={evergo_user.pk} ({email}): owner is required (user/group)."
                    )
                )
                continue

            try:
                remote_uid = self._resolve_remote_user_uid(profile, email)
                if remote_uid is None:
                    remote_uid = profile.execute(
                        "res.users",
                        "create",
                        {
                            "name": evergo_user.name or email,
                            "login": email,
                            "email": email,
                        },
                    )
                if not isinstance(remote_uid, int):
                    raise ValueError("Odoo did not return a valid integer user id.")

                with transaction.atomic():
                    _, was_created = OdooEmployee.objects.update_or_create(
                        host=profile.host,
                        database=profile.database,
                        odoo_uid=remote_uid,
                        defaults={
                            "user": evergo_user.user,
                            "group": evergo_user.group,
                            "username": email,
                            "password": "",
                            "name": evergo_user.name or email,
                            "email": email,
                        },
                    )
                created += int(was_created)
                skipped += int(not was_created)
            except (IntegrityError, ValueError) as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipping Evergo user id={evergo_user.pk} ({email}): {exc}"
                    )
                )
                errors += 1
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Odoo sync failed for Evergo user id={evergo_user.pk} ({email}): {type(exc).__name__}: {exc}"
                    )
                )
                errors += 1
                break

        self.stdout.write(
            self.style.SUCCESS(
                "Evergo-to-Odoo sync completed: "
                f"created={created}, skipped={skipped}, errors={errors}"
            )
        )

    def _resolve_remote_user_uid(self, profile: OdooEmployee, email: str) -> int | None:
        """Return an existing remote Odoo user id for the given email/login, if present."""

        users = profile.execute(
            "res.users",
            "search_read",
            [
                "|",
                ("login", "=", email),
                ("email", "=", email),
            ],
            fields=["id"],
            limit=1,
        )
        if not users:
            return None
        candidate = users[0].get("id")
        if not isinstance(candidate, int):
            raise ValueError("Odoo search did not return a valid integer user id.")
        return candidate

    def _resolve_profile(self, options: dict[str, Any]) -> OdooEmployee:
        """Return the chosen Odoo profile or the first verified profile."""

        profile_id = options.get("profile_id")
        if profile_id is not None:
            try:
                return OdooEmployee.objects.get(pk=profile_id)
            except OdooEmployee.DoesNotExist as exc:
                raise CommandError(
                    f"Odoo profile id={profile_id} does not exist."
                ) from exc

        profile = (
            OdooEmployee.objects.filter(verified_on__isnull=False)
            .order_by("pk")
            .first()
        )
        if profile is None:
            raise CommandError(
                "No verified Odoo profile is available. Use --profile-id with an existing record or verify credentials first."
            )
        return profile

    def _parse_json_list(self, raw_value: str, *, argument_name: str) -> list[Any]:
        """Parse a JSON list argument and raise a command error on malformed input."""

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON for {argument_name}: {exc.msg}") from exc

        if not isinstance(parsed, list):
            raise CommandError(f"{argument_name} must be a JSON list.")
        return parsed

    def _parse_json_dict(self, raw_value: str, *, argument_name: str) -> dict[str, Any]:
        """Parse a JSON object argument and raise a command error on malformed input."""

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON for {argument_name}: {exc.msg}") from exc

        if not isinstance(parsed, dict):
            raise CommandError(f"{argument_name} must be a JSON object.")
        return parsed
