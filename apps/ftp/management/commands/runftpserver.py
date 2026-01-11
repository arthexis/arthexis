from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models

from apps.nodes.models import Node

from ...authorizers import DjangoFTPAuthorizer
from ...models import FTPFolder, FTPServer
from ...utils import build_user_mounts


class Command(BaseCommand):
    help = "Run an embedded FTP server that reuses Django user credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--port",
            type=int,
            help="Port to listen on. Defaults to the configured FTP server port.",
        )
        parser.add_argument(
            "--bind",
            dest="bind_address",
            default=None,
            help="Interface to bind. Defaults to the configured bind address.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build mounts and display the plan without starting the server.",
        )

    def handle(self, *args, **options):
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.log import config_logging
        from pyftpdlib.servers import FTPServer as PyFTPServer

        node = Node.get_local()
        server_config = self._resolve_server_config(node)
        if server_config and not server_config.enabled and not options["dry_run"]:
            self.stdout.write(
                self.style.WARNING("FTP server is disabled. Use --dry-run to preview."),
            )
            return

        folders = self._eligible_folders(node)
        mount_root = Path(settings.BASE_DIR) / ".ftp_mounts"
        mounts, warnings = build_user_mounts(folders, mount_root)
        for message in warnings:
            self.stdout.write(self.style.WARNING(message))

        if not mounts:
            self.stdout.write(self.style.WARNING("No FTP mounts available."))
            return

        bind_address = options["bind_address"] or (server_config.bind_address if server_config else "0.0.0.0")
        port = options["port"] or (server_config.port if server_config else 2121)
        passive_range = server_config.resolved_passive_ports() if server_config else None

        authorizer = DjangoFTPAuthorizer(mounts)
        handler = FTPHandler
        handler.authorizer = authorizer
        if passive_range:
            handler.passive_ports = range(passive_range[0], passive_range[1] + 1)

        if options["dry_run"]:
            self._print_plan(bind_address, port, mounts)
            return

        config_logging(level="INFO")
        self.stdout.write(self.style.SUCCESS(f"Starting FTP server on {bind_address}:{port}"))
        server = PyFTPServer((bind_address, port), handler)
        server.serve_forever()

    def _eligible_folders(self, node):
        folders = FTPFolder.objects.filter(enabled=True).select_related("user", "group")
        if node:
            folders = folders.filter(models.Q(node=node) | models.Q(node__isnull=True))
        else:
            folders = folders.filter(node__isnull=True)
        return folders

    def _resolve_server_config(self, node) -> FTPServer | None:
        if node:
            server_config = FTPServer.objects.filter(models.Q(node=node) | models.Q(node__isnull=True)).order_by("-node_id").first()
        else:
            server_config = FTPServer.objects.filter(node__isnull=True).first()
        return server_config

    def _print_plan(self, bind_address: str, port: int, mounts):
        self.stdout.write(
            self.style.SUCCESS(
                f"Prepared {len(mounts)} FTP account(s) for {bind_address}:{port}"
            )
        )
        for username, mount in mounts.items():
            self.stdout.write(f"- {username}: home={mount.home} perms={mount.permissions}")
            for binding in mount.bindings:
                self.stdout.write(
                    f"    -> {binding.link_name} -> {binding.target} ({binding.permissions})"
                )
