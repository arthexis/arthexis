from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@dataclass(frozen=True)
class FeatureKind:
    """Constants for supported feature kinds."""

    SUITE: str = "suite"
    NODE: str = "node"


class Command(BaseCommand):
    """Manage suite and node features from the command line."""

    help = (
        "List enabled suite/node features by default. "
        "Use --kind plus --all/--enable/--disable for specific workflows."
    )

    def add_arguments(self, parser) -> None:
        """Register command arguments."""

        parser.add_argument(
            "--kind",
            choices=[FeatureKind.SUITE, FeatureKind.NODE],
            help="Feature kind to scope operations to (suite or node).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="List all available features for the selected kind(s).",
        )
        parser.add_argument(
            "--enable",
            help="Enable a feature by slug (requires --kind).",
        )
        parser.add_argument(
            "--disable",
            help="Disable a feature by slug (requires --kind).",
        )

    def handle(self, *args, **options) -> None:
        """Execute feature listing/toggle behavior."""

        kind = options["kind"]
        enable_slug = options["enable"]
        disable_slug = options["disable"]

        if enable_slug and disable_slug:
            raise CommandError("Choose only one of --enable or --disable.")

        if (enable_slug or disable_slug) and not kind:
            raise CommandError("--kind is required when using --enable or --disable.")

        if enable_slug:
            self._toggle_feature(kind=kind, slug=enable_slug, enabled=True)
        elif disable_slug:
            self._toggle_feature(kind=kind, slug=disable_slug, enabled=False)

        self._list_features(kind=kind, include_all=options["all"])

    def _toggle_feature(self, *, kind: str, slug: str, enabled: bool) -> None:
        """Enable or disable a feature for the requested feature kind."""

        if kind == FeatureKind.SUITE:
            feature = Feature.objects.filter(slug=slug).first()
            if feature is None:
                raise CommandError(f"Unknown suite feature: {slug}")
            feature.is_enabled = enabled
            feature.save(update_fields=["is_enabled", "updated_at"])
            action = "Enabled" if enabled else "Disabled"
            self.stdout.write(self.style.SUCCESS(f"{action} suite feature '{slug}'."))
            return

        if kind == FeatureKind.NODE:
            self._toggle_node_feature(slug=slug, enabled=enabled)
            return

        raise CommandError(f"Unsupported feature kind: {kind}")

    def _toggle_node_feature(self, *, slug: str, enabled: bool) -> None:
        """Enable or disable a node feature assignment for the local node."""

        feature = NodeFeature.objects.filter(slug=slug).first()
        if feature is None:
            raise CommandError(f"Unknown node feature: {slug}")

        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for node feature operations.")

        if enabled:
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(self.style.SUCCESS(f"Enabled node feature '{slug}'."))
            return

        deleted, _detail = NodeFeatureAssignment.objects.filter(
            node=node,
            feature=feature,
        ).delete()
        if deleted:
            self.stdout.write(self.style.SUCCESS(f"Disabled node feature '{slug}'."))
        else:
            self.stdout.write(self.style.WARNING(f"Node feature '{slug}' is already disabled."))

    def _list_features(self, *, kind: str | None, include_all: bool) -> None:
        """Render suite and/or node feature lists."""

        kinds = [kind] if kind else [FeatureKind.SUITE, FeatureKind.NODE]
        for index, selected_kind in enumerate(kinds):
            if index:
                self.stdout.write("")
            if selected_kind == FeatureKind.SUITE:
                self._list_suite_features(include_all=include_all)
            elif selected_kind == FeatureKind.NODE:
                self._list_node_features(include_all=include_all)
            else:
                raise CommandError(f"Unsupported feature kind: {selected_kind}")

    def _list_suite_features(self, *, include_all: bool) -> None:
        """Render suite feature rows."""

        queryset = Feature.objects.order_by("slug")
        if not include_all:
            queryset = queryset.filter(is_enabled=True)
        self.stdout.write(self.style.MIGRATE_HEADING("Suite features"))
        if not queryset.exists():
            self.stdout.write("(none)")
            return

        for feature in queryset:
            status = "enabled" if feature.is_enabled else "disabled"
            self.stdout.write(f"- {feature.slug} [{status}]")

    def _list_node_features(self, *, include_all: bool) -> None:
        """Render node feature rows using local node assignments."""

        node = Node.get_local()
        self.stdout.write(self.style.MIGRATE_HEADING("Node features"))
        if node is None:
            if include_all:
                for feature in NodeFeature.objects.order_by("slug"):
                    self.stdout.write(f"- {feature.slug} [disabled]")
                if not NodeFeature.objects.exists():
                    self.stdout.write("(none)")
                return
            self.stdout.write("(no local node)")
            return

        assigned = set(node.features.values_list("slug", flat=True))
        queryset = NodeFeature.objects.order_by("slug")
        if not include_all:
            queryset = queryset.filter(slug__in=assigned)

        if not queryset.exists():
            self.stdout.write("(none)")
            return

        for feature in queryset:
            status = "enabled" if feature.slug in assigned else "disabled"
            self.stdout.write(f"- {feature.slug} [{status}]")
