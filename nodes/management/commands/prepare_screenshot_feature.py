import sys

from django.contrib import messages
from django.core.management.base import BaseCommand

from nodes.feature_checks import feature_checks
from nodes.models import Node, NodeFeature, NodeFeatureAssignment, NodeRole


class Command(BaseCommand):
    help = (
        "Ensure the screenshot-poll feature is configured for the local node and "
        "report eligibility."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--role",
            default="Terminal",
            help="Name of the node role to associate with screenshot captures.",
        )

    def handle(self, *args, **options):
        role_name: str = options["role"]
        role, _ = NodeRole.objects.get_or_create(name=role_name)

        feature, created = NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created screenshot-poll feature."))

        if not feature.roles.filter(pk=role.pk).exists():
            feature.roles.add(role)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Linked {feature.display} to the {role.name} role for manual enablement."
                )
            )

        node, _ = Node.register_current(notify_peers=False)
        if node is None:
            self.stderr.write(
                self.style.ERROR(
                    "Unable to register the local node; ensure the database is available."
                )
            )
            sys.exit(1)
        if node.role is None:
            node.role = role
            node.save(update_fields=["role"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Assigned {role.name} role to local node {node.hostname}."
                )
            )

        if not node.has_feature(feature.slug):
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enabled {feature.display} on local node {node.hostname}."
                )
            )

        result = feature_checks.run(feature, node=node)
        if result is None:
            self.stdout.write(
                self.style.WARNING(
                    "No eligibility check is configured for screenshot captures."
                )
            )
            return

        style = self.style.SUCCESS if result.success else self.style.WARNING
        self.stdout.write(style(result.message))
        if result.level >= messages.ERROR:
            sys.exit(1)
