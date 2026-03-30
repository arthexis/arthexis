"""Operational command for AWS Lightsail-backed deploy setup."""

from __future__ import annotations

import getpass
import os
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

from apps.aws.lightsail_regions import COMMON_LIGHTSAIL_REGIONS
from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import (
    LightsailFetchError,
    create_lightsail_instance,
    delete_lightsail_instance,
    fetch_lightsail_instance,
    issue_mfa_session_credentials,
    list_lightsail_regions,
    parse_instance_details,
)
from apps.deploy.models import DeployInstance, DeployRun, DeployServer
from apps.features.utils import is_suite_feature_enabled


LIGHTSAIL_CLI_AUTH_BOOTSTRAP_FEATURE_SLUG = "deploy-lightsail-cli-auth-bootstrap"


class Command(BaseCommand):
    """Create or refresh Lightsail-backed deploy records from the CLI."""

    help = "Create/find an AWS Lightsail instance and register deploy records."

    def add_arguments(self, parser):
        """Register setup options."""

        region_choices = self._region_choices()
        default_region = "us-east-1"
        env_region = (
            os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or ""
        ).strip()
        if env_region in region_choices:
            default_region = env_region
        parser.add_argument(
            "--credentials", required=True, help="AWS credential id or name."
        )
        parser.add_argument(
            "--region",
            default=default_region,
            choices=region_choices,
            help="Lightsail region code (default: us-east-1).",
        )
        parser.add_argument(
            "--instance",
            "--instance-name",
            required=True,
            dest="instance",
            help="Lightsail instance name.",
        )
        parser.add_argument(
            "--blueprint-id", default="", help="Lightsail blueprint id."
        )
        parser.add_argument("--bundle-id", default="", help="Lightsail bundle id.")
        parser.add_argument(
            "--key-pair",
            "--key-pair-name",
            default="",
            dest="key_pair",
            help="Lightsail SSH key pair.",
        )
        parser.add_argument(
            "--availability-zone",
            default="",
            help="Optional availability zone (for example us-east-1a).",
        )
        parser.add_argument(
            "--skip-create",
            action="store_true",
            help="Skip Lightsail create call and only fetch/register an existing instance.",
        )
        parser.add_argument(
            "--refresh-credentials",
            action="store_true",
            help="Re-enter and save the selected AWS credentials before Lightsail operations.",
        )
        parser.add_argument(
            "--access-key-id",
            default="",
            help="Optional AWS access key id used with --refresh-credentials.",
        )
        parser.add_argument(
            "--secret-access-key",
            default="",
            help="Optional AWS secret access key used with --refresh-credentials.",
        )
        parser.add_argument(
            "--deploy-instance",
            "--deploy-instance-name",
            default="main",
            dest="deploy_instance",
            help="Deploy instance label stored under the deploy server (default: main).",
        )
        parser.add_argument(
            "--install-dir",
            default="",
            help="Absolute install directory for the Arthexis checkout. Defaults to /srv/<instance-name>.",
        )
        parser.add_argument(
            "--service",
            "--service-name",
            default="",
            dest="service",
            help="System service name for Arthexis. Defaults to arthexis-<instance-name>.",
        )
        parser.add_argument("--branch", default="main", help="Git branch to track.")
        parser.add_argument(
            "--ocpp-port",
            type=int,
            default=9000,
            help="OCPP/WebSocket port for this instance (default: 9000).",
        )
        parser.add_argument("--admin-url", default="", help="Optional admin URL.")
        parser.add_argument("--env-file", default="", help="Optional env file path.")
        parser.add_argument(
            "--mfa-serial",
            default="",
            help="AWS MFA device ARN/serial for accounts that require MFA.",
        )
        parser.add_argument(
            "--mfa-code",
            default="",
            help="Optional MFA token code. If omitted while --mfa-serial is set, prompt interactively.",
        )
        parser.add_argument(
            "--mfa-duration-seconds",
            type=int,
            default=900,
            help="STS session duration in seconds when MFA is used (minimum 900).",
        )
        parser.add_argument("--ssh-user", default="ubuntu", help="SSH username.")
        parser.add_argument("--ssh-port", type=int, default=22, help="SSH port.")

    def handle(self, *args, **options):
        """Create/fetch Lightsail instance and wire deploy models."""

        credentials = self._resolve_credentials(str(options["credentials"]))
        if options["refresh_credentials"]:
            credentials = self._refresh_credentials(
                credentials,
                access_key_id=str(options.get("access_key_id") or "").strip(),
                secret_access_key=str(options.get("secret_access_key") or "").strip(),
            )
        region = str(options["region"]).strip()
        instance_name = str(options["instance"]).strip()
        deploy_instance_name = str(options["deploy_instance"]).strip()
        self._validate_local_prerequisites()
        install_dir = (
            str(options.get("install_dir") or "").strip() or f"/srv/{instance_name}"
        )
        service_name = (
            str(options.get("service") or "").strip() or f"arthexis-{instance_name}"
        )
        auth_kwargs = self._resolve_aws_auth_kwargs(
            credentials=credentials,
            region=region,
            mfa_serial=options["mfa_serial"].strip(),
            mfa_code=options["mfa_code"].strip(),
            mfa_duration_seconds=options["mfa_duration_seconds"],
        )
        skip_create = bool(options.get("skip_create"))
        created_remote_instance = False
        persisted_records = False
        details: dict[str, object] = {}

        if not skip_create:
            blueprint_id = str(options.get("blueprint_id") or "").strip()
            bundle_id = str(options.get("bundle_id") or "").strip()
            if not blueprint_id or not bundle_id:
                raise CommandError(
                    "--blueprint-id and --bundle-id are required unless --skip-create is set."
                )
            try:
                details = create_lightsail_instance(
                    name=instance_name,
                    region=region,
                    blueprint_id=blueprint_id,
                    bundle_id=bundle_id,
                    key_pair_name=str(options.get("key_pair") or "").strip() or None,
                    availability_zone=str(
                        options.get("availability_zone") or ""
                    ).strip()
                    or None,
                    **auth_kwargs,
                )
            except LightsailFetchError as exc:
                raise CommandError(
                    f"Unable to create Lightsail instance: {exc}"
                ) from exc
            created_remote_instance = True

        try:
            if skip_create or not details:
                try:
                    details = fetch_lightsail_instance(
                        name=instance_name,
                        region=region,
                        **auth_kwargs,
                    )
                except LightsailFetchError as exc:
                    raise CommandError(
                        f"Unable to fetch Lightsail instance details: {exc}"
                    ) from exc

            if not details:
                raise CommandError(
                    "Lightsail instance details were empty; setup cannot continue."
                )

            host = (
                details.get("publicIpAddress") or details.get("privateIpAddress") or ""
            ).strip()
            if not host:
                raise CommandError(
                    "Lightsail instance has no public/private IP yet; try again shortly."
                )

            lightsail_defaults = parse_instance_details(details)
            lightsail_defaults["credentials"] = credentials

            with transaction.atomic():
                lightsail_instance, _ = LightsailInstance.objects.update_or_create(
                    name=instance_name,
                    region=region,
                    defaults=lightsail_defaults,
                )
                deploy_server, _ = DeployServer.objects.update_or_create(
                    name=instance_name,
                    defaults={
                        "provider": DeployServer.Provider.AWS_LIGHTSAIL,
                        "region": region,
                        "host": host,
                        "ssh_port": int(options["ssh_port"]),
                        "ssh_user": str(options["ssh_user"]).strip(),
                        "lightsail_instance": lightsail_instance,
                        "is_enabled": True,
                    },
                )
                deploy_instance, _ = DeployInstance.objects.update_or_create(
                    server=deploy_server,
                    name=deploy_instance_name,
                    defaults={
                        "install_dir": install_dir,
                        "service_name": service_name,
                        "env_file": str(options.get("env_file") or "").strip(),
                        "branch": str(options.get("branch") or "main").strip()
                        or "main",
                        "ocpp_port": int(options["ocpp_port"]),
                        "admin_url": str(options.get("admin_url") or "").strip(),
                        "is_enabled": True,
                    },
                )
                DeployRun.objects.create(
                    instance=deploy_instance,
                    action=DeployRun.Action.DEPLOY,
                    status=DeployRun.Status.PENDING,
                    requested_by="lightsail",
                    output="CLI Lightsail setup prepared deployment records.",
                )
            persisted_records = True
        except (OperationalError, ProgrammingError) as exc:
            if created_remote_instance and not persisted_records:
                self._cleanup_remote_instance(
                    instance_name=instance_name,
                    region=region,
                    auth_kwargs=auth_kwargs,
                )
            raise CommandError(
                "Required deployment tables are not available. Run migrations first."
            ) from exc
        except Exception:
            if created_remote_instance and not persisted_records:
                self._cleanup_remote_instance(
                    instance_name=instance_name,
                    region=region,
                    auth_kwargs=auth_kwargs,
                )
            raise

        self.stdout.write(
            self.style.SUCCESS("Lightsail deployment records configured.")
        )
        self.stdout.write(f"Server: {instance_name} ({region}) host={host}")
        self.stdout.write(
            f"Deploy instance: {deploy_instance_name} service={service_name} install_dir={install_dir}"
        )

    def _region_choices(self) -> tuple[str, ...]:
        """Return normalized Lightsail region choices for CLI validation."""

        discovered: list[str] = []
        try:
            discovered = list_lightsail_regions()
        except Exception:
            discovered = []
        merged = sorted({*COMMON_LIGHTSAIL_REGIONS, *discovered})
        return tuple(merged)

    def _cleanup_remote_instance(
        self,
        *,
        instance_name: str,
        region: str,
        auth_kwargs: dict[str, object],
    ) -> None:
        """Best-effort cleanup when post-create setup fails before persistence."""

        try:
            delete_lightsail_instance(
                name=instance_name,
                region=region,
                **auth_kwargs,
            )
        except LightsailFetchError as exc:
            self.stderr.write(
                self.style.WARNING(
                    "Warning: failed to cleanup created Lightsail instance "
                    f"'{instance_name}' in '{region}': {exc}"
                )
            )

    def _validate_local_prerequisites(self) -> None:
        """Fail fast when deployment tables are unavailable."""

        try:
            AWSCredentials.objects.exists()
            LightsailInstance.objects.exists()
            DeployInstance.objects.exists()
            DeployRun.objects.exists()
            DeployServer.objects.exists()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError(
                "Required deployment tables are not available. Run migrations first."
            ) from exc

    def _resolve_credentials(self, raw_credentials: str) -> AWSCredentials:
        """Resolve AWS credentials by integer id or by name, prompting to create when missing."""

        candidate = raw_credentials.strip()
        if not candidate:
            raise CommandError("--credentials is required.")

        try:
            creds = None
            if candidate.isdigit():
                creds = AWSCredentials.objects.filter(pk=int(candidate)).first()

            if not creds:
                creds = AWSCredentials.objects.filter(name=candidate).first()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError(
                "AWS credential tables are not available. Run migrations first."
            ) from exc

        if creds:
            return creds

        if candidate.isdigit():
            raise CommandError(f"AWS credentials not found for '{raw_credentials}'.")

        if not is_suite_feature_enabled(
            LIGHTSAIL_CLI_AUTH_BOOTSTRAP_FEATURE_SLUG, default=True
        ):
            raise CommandError(
                "AWS credentials not found and CLI credential bootstrap is disabled by suite feature."
            )

        self.stdout.write(
            self.style.WARNING(
                f"AWS credentials '{candidate}' were not found. Creating a new credential record."
            )
        )
        access_key_id = self._prompt_required("AWS access key id")
        secret_access_key = self._prompt_required("AWS secret access key", secret=True)
        try:
            return AWSCredentials.objects.create(
                name=candidate,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            )
        except IntegrityError as exc:
            raise CommandError(
                f"Unable to create AWS credentials '{candidate}'. "
                "The access key may already exist or values are invalid."
            ) from exc

    def _refresh_credentials(
        self,
        credentials: AWSCredentials,
        *,
        access_key_id: str,
        secret_access_key: str,
    ) -> AWSCredentials:
        """Update stored credentials using provided values or interactive prompts."""

        provided_access = access_key_id.strip()
        provided_secret = secret_access_key.strip()
        if bool(provided_access) != bool(provided_secret):
            raise CommandError(
                "--access-key-id and --secret-access-key must be provided together."
            )

        if provided_access and provided_secret:
            new_access_key_id = provided_access
            new_secret_access_key = provided_secret
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Refreshing AWS credentials for '{credentials.name}'. Enter replacement values."
                )
            )
            new_access_key_id = self._prompt_required("AWS access key id")
            new_secret_access_key = self._prompt_required(
                "AWS secret access key", secret=True
            )

        credentials.access_key_id = new_access_key_id
        credentials.secret_access_key = new_secret_access_key
        try:
            credentials.save(update_fields=["access_key_id", "secret_access_key"])
        except IntegrityError as exc:
            raise CommandError(
                f"Unable to update AWS credentials '{credentials.name}'. "
                "The access key may already exist or values are invalid."
            ) from exc
        self.stdout.write(
            self.style.SUCCESS(f"Updated AWS credentials '{credentials.name}'")
        )
        return credentials

    def _prompt_required(self, label: str, *, secret: bool = False) -> str:
        """Prompt for a required non-empty value, optionally hiding input."""

        if not sys.stdin.isatty():
            raise CommandError(
                f"{label} is required, but interactive prompts are unavailable in non-interactive mode."
            )

        prompt_func = getpass.getpass if secret else input
        value = ""
        while not value:
            try:
                value = prompt_func(f"{label}: ").strip()
            except EOFError as exc:
                raise CommandError(
                    f"{label} is required, but no interactive input was received."
                ) from exc
        return value

    def _resolve_aws_auth_kwargs(
        self,
        *,
        credentials: AWSCredentials,
        region: str,
        mfa_serial: str,
        mfa_code: str,
        mfa_duration_seconds: int,
    ) -> dict[str, object]:
        """Return AWS auth kwargs, optionally converting long-lived keys into MFA session creds."""

        if not mfa_serial:
            return {"credentials": credentials}

        if not is_suite_feature_enabled(
            LIGHTSAIL_CLI_AUTH_BOOTSTRAP_FEATURE_SLUG, default=True
        ):
            raise CommandError("MFA CLI auth bootstrap is disabled by suite feature.")

        token_code = mfa_code.strip() or self._prompt_required("AWS MFA code")
        try:
            session_credentials = issue_mfa_session_credentials(
                region=region,
                credentials=credentials,
                mfa_serial=mfa_serial,
                mfa_code=token_code,
                duration_seconds=mfa_duration_seconds,
            )
        except LightsailFetchError as exc:
            raise CommandError(
                f"Unable to issue AWS session credentials with MFA: {exc}"
            ) from exc
        return session_credentials
