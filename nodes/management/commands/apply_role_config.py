from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from nodes.models import Node, NodeConfigurationJob
from nodes.tasks import run_role_configuration


class Command(BaseCommand):
    help = "Apply the active role configuration playbook to a node."

    def add_arguments(self, parser):
        parser.add_argument(
            "--node",
            dest="node_identifier",
            help=(
                "Node identifier to configure. Accepts a primary key, public "
                "endpoint, hostname, or UUID. Defaults to the local node."
            ),
        )
        parser.add_argument(
            "--user",
            dest="username",
            help="Username to attribute as the manual trigger.",
        )

    def handle(self, *args, **options):
        identifier = options.get("node_identifier")
        username = options.get("username")

        node = self._resolve_node(identifier)
        if node is None:
            if identifier:
                raise CommandError(f"Node '{identifier}' could not be found.")
            raise CommandError(
                "No node identifier provided and the local node is not registered."
            )

        user = None
        if username:
            User = get_user_model()
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"User '{username}' could not be found.")

        job = run_role_configuration(
            node,
            trigger=NodeConfigurationJob.Trigger.MANUAL,
            user_id=getattr(user, "pk", None),
        )

        if job.status == NodeConfigurationJob.Status.SUCCESS:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Configuration applied successfully (job #{job.pk})."
                )
            )
            return

        message = job.status_message or "Configuration failed."
        if job.stderr:
            self.stderr.write(job.stderr)
        raise CommandError(f"Configuration failed (job #{job.pk}): {message}")

    def _resolve_node(self, identifier: str | None) -> Node | None:
        if not identifier:
            return Node.get_local()

        try:
            pk = int(identifier)
        except (TypeError, ValueError):
            pk = None
        if pk is not None:
            node = Node.objects.filter(pk=pk).first()
            if node:
                return node

        candidate = Node.objects.filter(public_endpoint__iexact=identifier).first()
        if candidate:
            return candidate

        candidate = Node.objects.filter(hostname__iexact=identifier).first()
        if candidate:
            return candidate

        candidate = Node.objects.filter(network_hostname__iexact=identifier).first()
        if candidate:
            return candidate

        try:
            node_uuid = uuid.UUID(str(identifier))
        except (TypeError, ValueError):
            node_uuid = None
        if node_uuid is not None:
            candidate = Node.objects.filter(uuid=node_uuid).first()
            if candidate:
                return candidate

        return None
