from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.features.management.feature_ops import (
    FeatureKind,
    get_feature_state,
    infer_kind_for_slug,
    refresh_local_node_features,
    set_feature_enabled,
)


class Command(BaseCommand):
    """Inspect or toggle a single suite/node feature by slug."""

    help = (
        "Operate on a single feature. Pass the feature slug as the first argument, "
        "then use --enabled/--disabled to toggle state."
    )

    def add_arguments(self, parser) -> None:
        """Register command arguments."""

        parser.add_argument(
            "slug", nargs="?", help="Feature slug to inspect or modify."
        )
        parser.add_argument(
            "--kind",
            choices=[FeatureKind.SUITE, FeatureKind.NODE],
            help="Feature kind to scope operations to (suite or node).",
        )
        parser.add_argument(
            "--enabled",
            action="store_true",
            help="Enable the selected feature.",
        )
        parser.add_argument(
            "--disabled",
            action="store_true",
            help="Disable the selected feature.",
        )
        parser.add_argument(
            "--refresh-node",
            action="store_true",
            help="Refresh auto-managed feature assignments for the local node.",
        )

    def handle(self, *args, **options) -> None:
        """Execute single-feature inspection/toggle behavior."""

        slug = options.get("slug")
        kind = options.get("kind")
        enable_selected = bool(options.get("enabled"))
        disable_selected = bool(options.get("disabled"))
        refresh_node = bool(options.get("refresh_node"))

        if not slug and not refresh_node:
            raise CommandError("Provide a feature slug or use --refresh-node.")

        if enable_selected and disable_selected:
            raise CommandError("Choose only one of --enabled or --disabled.")

        if refresh_node:
            node = refresh_local_node_features()
            if node is None:
                self.stdout.write(
                    self.style.WARNING(
                        "Local node not found, skipping feature refresh."
                    )
                )
            else:
                self.stdout.write(f"Refreshing features for local node {node}...")
                self.stdout.write(
                    self.style.SUCCESS("Successfully refreshed features.")
                )

        if slug:
            resolved_kind = infer_kind_for_slug(slug=slug, kind=kind)

            if enable_selected or disable_selected:
                set_feature_enabled(
                    slug=slug,
                    enabled=enable_selected,
                    kind=resolved_kind,
                )

            _kind, is_enabled = get_feature_state(slug=slug, kind=resolved_kind)
            status = "enabled" if is_enabled else "disabled"
            self.stdout.write(f"- {slug} [{status}]")
