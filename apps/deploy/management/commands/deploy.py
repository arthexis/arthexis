"""Operational deployment command for the Deploy app."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import (
    LightsailFetchError,
    create_lightsail_instance,
    fetch_lightsail_instance,
    parse_instance_details,
)
from apps.deploy.models import DeployInstance, DeployRun, DeployServer


class Command(BaseCommand):
    """Show deploy status and bootstrap AWS Lightsail-backed deployment records."""

    help = "Show deployment status or bootstrap a Lightsail deployment from the CLI."

    def add_arguments(self, parser):
        """Register command options and subcommands."""

        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="How many recent runs to show for status mode (default: 10).",
        )

        subparsers = parser.add_subparsers(dest="action")
        setup_parser = subparsers.add_parser(
            "setup-lightsail",
            help="Create/find an AWS Lightsail instance and register deploy records.",
        )
        setup_parser.add_argument("--credentials", required=True, help="AWS credential id or name.")
        setup_parser.add_argument("--region", required=True, help="Lightsail region code.")
        setup_parser.add_argument("--instance-name", required=True, help="Lightsail instance name.")
        setup_parser.add_argument("--blueprint-id", required=True, help="Lightsail blueprint id.")
        setup_parser.add_argument("--bundle-id", required=True, help="Lightsail bundle id.")
        setup_parser.add_argument("--key-pair-name", default="", help="Lightsail SSH key pair.")
        setup_parser.add_argument(
            "--availability-zone",
            default="",
            help="Optional availability zone (for example us-east-1a).",
        )
        setup_parser.add_argument(
            "--skip-create",
            action="store_true",
            help="Skip Lightsail create call and only fetch/register an existing instance.",
        )
        setup_parser.add_argument(
            "--deploy-instance-name",
            default="main",
            help="Deploy instance label stored under the deploy server (default: main).",
        )
        setup_parser.add_argument(
            "--install-dir",
            default="",
            help="Absolute install directory for the Arthexis checkout. Defaults to /srv/<instance-name>.",
        )
        setup_parser.add_argument(
            "--service-name",
            default="",
            help="System service name for Arthexis. Defaults to arthexis-<instance-name>.",
        )
        setup_parser.add_argument("--branch", default="main", help="Git branch to track.")
        setup_parser.add_argument(
            "--ocpp-port",
            type=int,
            default=9000,
            help="OCPP/WebSocket port for this instance (default: 9000).",
        )
        setup_parser.add_argument("--admin-url", default="", help="Optional admin URL.")
        setup_parser.add_argument("--env-file", default="", help="Optional env file path.")
        setup_parser.add_argument("--ssh-user", default="ubuntu", help="SSH username.")
        setup_parser.add_argument("--ssh-port", type=int, default=22, help="SSH port.")

    def handle(self, *args, **options):
        """Dispatch to status view or setup flow."""

        action = options.get("action")
        if action == "setup-lightsail":
            self._handle_setup_lightsail(options)
            return
        self._handle_status(limit=max(1, int(options["limit"])))

    def _handle_status(self, *, limit: int) -> None:
        """Render deployment summary information for operators."""

        try:
            instances = list(
                DeployInstance.objects.select_related("server").order_by("server__name", "name")
            )
        except (OperationalError, ProgrammingError):
            self.stdout.write("Deployment tables are not available yet.")
            self.stdout.write("Run migrations before using the deploy command.")
            return

        if not instances:
            self.stdout.write("No deployment instances configured yet.")
            self.stdout.write("Use admin Deploy models to register servers and instances.")
            return

        self.stdout.write("Configured deployment instances:")
        for instance in instances:
            status = "enabled" if instance.is_enabled else "disabled"
            self.stdout.write(
                f"- {instance.server.name}:{instance.name} [{status}] "
                f"service={instance.service_name} dir={instance.install_dir}"
            )

        recent_runs = list(
            DeployRun.objects.select_related("instance", "instance__server", "release")[:limit]
        )
        self.stdout.write("")
        self.stdout.write(f"Recent deploy runs (latest {limit}):")
        if not recent_runs:
            self.stdout.write("- No deployment runs recorded yet.")
            return

        for run in recent_runs:
            release = run.release.version if run.release else "-"
            self.stdout.write(
                f"- #{run.pk} {run.instance.server.name}:{run.instance.name} "
                f"action={run.action} status={run.status} release={release}"
            )

    def _handle_setup_lightsail(self, options: dict[str, object]) -> None:
        """Create/fetch Lightsail instance and wire Deploy models for CLI-driven setup."""

        credentials = self._resolve_credentials(str(options["credentials"]))
        region = str(options["region"]).strip()
        instance_name = str(options["instance_name"]).strip()
        deploy_instance_name = str(options["deploy_instance_name"]).strip()
        install_dir = str(options.get("install_dir") or "").strip()
        if not install_dir:
            install_dir = f"/srv/{instance_name}"
        service_name = str(options.get("service_name") or "").strip()
        if not service_name:
            service_name = f"arthexis-{instance_name}"

        if not bool(options.get("skip_create")):
            try:
                create_lightsail_instance(
                    name=instance_name,
                    region=region,
                    blueprint_id=str(options["blueprint_id"]).strip(),
                    bundle_id=str(options["bundle_id"]).strip(),
                    credentials=credentials,
                    key_pair_name=str(options.get("key_pair_name") or "").strip() or None,
                    availability_zone=str(options.get("availability_zone") or "").strip() or None,
                )
            except LightsailFetchError as exc:
                raise CommandError(f"Unable to create Lightsail instance: {exc}") from exc

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

        try:
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
                    requested_by="deploy setup-lightsail",
                    output="CLI Lightsail setup prepared deployment records.",
                )
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError("Required deployment tables are not available. Run migrations first.") from exc

        self.stdout.write(self.style.SUCCESS("Lightsail deployment records configured."))
        self.stdout.write(f"Server: {instance_name} ({region}) host={host}")
        self.stdout.write(
            f"Deploy instance: {deploy_instance_name} service={service_name} install_dir={install_dir}"
        )

    def _resolve_credentials(self, raw_credentials: str) -> AWSCredentials:
        """Resolve AWS credentials by integer id or by name."""

        candidate = raw_credentials.strip()
        if not candidate:
            raise CommandError("--credentials is required.")

        try:
            if candidate.isdigit():
                creds = AWSCredentials.objects.filter(pk=int(candidate)).first()
                if creds:
                    return creds

            creds = AWSCredentials.objects.filter(name=candidate).first()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError("AWS credential tables are not available. Run migrations first.") from exc
        if creds:
            return creds

        raise CommandError(f"AWS credentials not found for '{raw_credentials}'.")
