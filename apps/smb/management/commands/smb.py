"""Management command for SMB partition orchestration."""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch

from apps.smb.models import SMBPartition, SMBServer
from apps.smb.services import SMBDiscoveryError, configure_server, create_partition, discover_partitions


class Command(BaseCommand):
    """Manage SMB servers and partitions from the CLI."""

    help = "Discover local partitions and configure SMB server/share mappings."

    def add_arguments(self, parser) -> None:
        """Define smb command subcommands and options."""

        subparsers = parser.add_subparsers(dest="action", required=True)

        subparsers.add_parser("list", help="List configured SMB servers and partitions.")

        subparsers.add_parser("discover", help="Discover local block partitions using lsblk.")

        configure_parser = subparsers.add_parser("configure", help="Create or update an SMB server config.")
        configure_parser.add_argument("--name", required=True)
        configure_parser.add_argument("--host", required=True)
        configure_parser.add_argument("--port", type=int, default=445)
        configure_parser.add_argument("--username", default="")
        configure_parser.add_argument("--password", default="")
        configure_parser.add_argument("--domain", default="")

        create_parser = subparsers.add_parser("create", help="Create/update an SMB partition mapping.")
        create_parser.add_argument("--server", required=True)
        create_parser.add_argument("--name", required=True)
        create_parser.add_argument("--share", required=True)
        create_parser.add_argument("--path", required=True)
        create_parser.add_argument("--device", default="")
        create_parser.add_argument("--filesystem", default="")
        create_parser.add_argument("--size-bytes", type=int, default=None)
        create_parser.add_argument("--mount-options", default="rw")

    def handle(self, *args, **options) -> None:
        """Dispatch command execution by action."""

        action = options["action"]
        if action == "list":
            self._list_configuration()
            return
        if action == "discover":
            self._discover()
            return
        if action == "configure":
            server = configure_server(
                name=options["name"],
                host=options["host"],
                port=options["port"],
                username=options["username"],
                password=options["password"],
                domain=options["domain"],
            )
            self.stdout.write(self.style.SUCCESS(f"Configured SMB server: {server}"))
            return
        if action == "create":
            try:
                partition = create_partition(
                    server_name=options["server"],
                    partition_name=options["name"],
                    share_name=options["share"],
                    local_path=options["path"],
                    device=options["device"],
                    filesystem=options["filesystem"],
                    size_bytes=options["size_bytes"],
                    mount_options=options["mount_options"],
                )
            except SMBServer.DoesNotExist as exc:
                raise CommandError(f"SMB server '{options['server']}' was not found.") from exc
            self.stdout.write(self.style.SUCCESS(f"Configured SMB partition: {partition}"))
            return
        raise CommandError(f"Unsupported action '{action}'.")

    def _discover(self) -> None:
        """Print local partition details from system discovery."""

        try:
            partitions = discover_partitions()
        except SMBDiscoveryError as exc:
            raise CommandError(str(exc)) from exc

        if not partitions:
            self.stdout.write("No partitions discovered.")
            return

        for partition in partitions:
            self.stdout.write(
                f"{partition.device} fs={partition.filesystem or '(unknown)'} size={partition.size_bytes or '(unknown)'}"
            )

    def _list_configuration(self) -> None:
        """Print configured SMB servers and partition mappings."""

        servers = SMBServer.objects.prefetch_related(
            Prefetch("partitions", queryset=SMBPartition.objects.order_by("name"))
        ).order_by("name")
        if not servers:
            self.stdout.write("No SMB servers configured.")
            return

        for server in servers:
            self.stdout.write(f"{server.name}: {server.host}:{server.port}")
            partitions = list(server.partitions.all())
            if not partitions:
                self.stdout.write("  (no partitions)")
                continue
            for partition in partitions:
                self.stdout.write(
                    "  "
                    f"{partition.name} => //{server.host}/{partition.share_name} "
                    f"[{partition.local_path}]"
                )
