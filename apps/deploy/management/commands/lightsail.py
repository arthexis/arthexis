"""Operational command for AWS Lightsail-backed deploy setup."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import (
    LightsailFetchError,
    create_lightsail_instance,
    delete_lightsail_instance,
    fetch_lightsail_instance,
    parse_instance_details,
)
from apps.deploy.models import DeployInstance, DeployRun, DeployServer


class Command(BaseCommand):
    """Create or refresh Lightsail-backed deploy records from the CLI."""

    help = "Create/find an AWS Lightsail instance and register deploy records."

    def add_arguments(self, parser):
        """Register setup options."""

        parser.add_argument("--credentials", required=True, help="AWS credential id or name.")
        parser.add_argument("--region", required=True, help="Lightsail region code.")
        parser.add_argument("--instance-name", required=True, help="Lightsail instance name.")
        parser.add_argument("--blueprint-id", default="", help="Lightsail blueprint id.")
        parser.add_argument("--bundle-id", default="", help="Lightsail bundle id.")
        parser.add_argument("--key-pair-name", default="", help="Lightsail SSH key pair.")
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
            "--deploy-instance-name",
            default="main",
            help="Deploy instance label stored under the deploy server (default: main).",
        )
        parser.add_argument(
            "--install-dir",
            default="",
            help="Absolute install directory for the Arthexis checkout. Defaults to /srv/<instance-name>.",
        )
        parser.add_argument(
            "--service-name",
            default="",
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
        parser.add_argument("--ssh-user", default="ubuntu", help="SSH username.")
        parser.add_argument("--ssh-port", type=int, default=22, help="SSH port.")

    def handle(self, *args, **options):
        """Create/fetch Lightsail instance and wire deploy models."""

        credentials = self._resolve_credentials(str(options["credentials"]))
        region = str(options["region"]).strip()
        instance_name = str(options["instance_name"]).strip()
        deploy_instance_name = str(options["deploy_instance_name"]).strip()
        self._validate_local_prerequisites()
        install_dir = str(options.get("install_dir") or "").strip() or f"/srv/{instance_name}"
        service_name = str(options.get("service_name") or "").strip() or f"arthexis-{instance_name}"
        skip_create = bool(options.get("skip_create"))
        created_remote_instance = False
        persisted_records = False

        if not skip_create:
            blueprint_id = str(options.get("blueprint_id") or "").strip()
            bundle_id = str(options.get("bundle_id") or "").strip()
            if not blueprint_id or not bundle_id:
                raise CommandError(
                    "--blueprint-id and --bundle-id are required unless --skip-create is set."
                )
            try:
                create_lightsail_instance(
                    name=instance_name,
                    region=region,
                    blueprint_id=blueprint_id,
                    bundle_id=bundle_id,
                    credentials=credentials,
                    key_pair_name=str(options.get("key_pair_name") or "").strip() or None,
                    availability_zone=str(options.get("availability_zone") or "").strip() or None,
                )
            except LightsailFetchError as exc:
                raise CommandError(f"Unable to create Lightsail instance: {exc}") from exc
            created_remote_instance = True

        try:
            try:
                details = fetch_lightsail_instance(
                    name=instance_name,
                    region=region,
                    credentials=credentials,
                )
            except LightsailFetchError as exc:
                raise CommandError(f"Unable to fetch Lightsail instance details: {exc}") from exc

            if not details:
                raise CommandError("Lightsail instance details were empty; setup cannot continue.")

            host = (details.get("publicIpAddress") or details.get("privateIpAddress") or "").strip()
            if not host:
                raise CommandError("Lightsail instance has no public/private IP yet; try again shortly.")

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
                        "branch": str(options.get("branch") or "main").strip() or "main",
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
                    credentials=credentials,
                )
            raise CommandError("Required deployment tables are not available. Run migrations first.") from exc
        except Exception:
            if created_remote_instance and not persisted_records:
                self._cleanup_remote_instance(
                    instance_name=instance_name,
                    region=region,
                    credentials=credentials,
                )
            raise

        self.stdout.write(self.style.SUCCESS("Lightsail deployment records configured."))
        self.stdout.write(f"Server: {instance_name} ({region}) host={host}")
        self.stdout.write(
            f"Deploy instance: {deploy_instance_name} service={service_name} install_dir={install_dir}"
        )

    def _cleanup_remote_instance(
        self,
        *,
        instance_name: str,
        region: str,
        credentials: AWSCredentials,
    ) -> None:
        """Best-effort cleanup when post-create setup fails before persistence."""

        try:
            delete_lightsail_instance(
                name=instance_name,
                region=region,
                credentials=credentials,
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
            DeployInstance.objects.exists()
            DeployServer.objects.exists()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError("Required deployment tables are not available. Run migrations first.") from exc

    def _resolve_credentials(self, raw_credentials: str) -> AWSCredentials:
        """Resolve AWS credentials by integer id or by name."""

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
            raise CommandError("AWS credential tables are not available. Run migrations first.") from exc

        if not creds:
            raise CommandError(f"AWS credentials not found for '{raw_credentials}'.")
        return creds
