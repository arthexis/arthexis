from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.features.management.feature_ops import (
    FeatureKind,
    get_feature_state,
    list_node_features,
    list_suite_features,
    refresh_local_node_features,
    reset_all_suite_features,
    set_feature_enabled,
)


class Command(BaseCommand):
    """List suite/node features and support fixture-based reset workflows."""

    help = (
        "List features by default. Use --feature to target a single feature, "
        "--enabled/--disabled as status filters, and --reset-all to reload "
        "suite features from mainstream fixtures."
    )

    def add_arguments(self, parser) -> None:
        """Register command arguments."""

        parser.add_argument(
            "--kind",
            choices=[FeatureKind.SUITE, FeatureKind.NODE],
            help="Feature kind to scope listing operations to (suite or node).",
        )
        parser.add_argument(
            "--feature",
            help="Feature slug filter. With --enabled/--disabled, behaves like singular feature command.",
        )
        parser.add_argument(
            "--enabled",
            action="store_true",
            help="Filter listing to enabled features.",
        )
        parser.add_argument(
            "--disabled",
            action="store_true",
            help="Filter listing to disabled features.",
        )
        parser.add_argument(
            "--reset-all",
            action="store_true",
            help="Reload all suite features from mainstream fixtures.",
        )
        parser.add_argument(
            "--refresh-node",
            action="store_true",
            help="Refresh auto-managed feature assignments for the local node.",
        )

    def handle(self, *args, **options) -> None:
        """Execute feature listing, optional single-feature operation, and reset flow."""

        kind = options.get("kind")
        slug = options.get("feature")
        include_enabled = bool(options.get("enabled"))
        include_disabled = bool(options.get("disabled"))
        reset_all = bool(options.get("reset_all"))
        refresh_node = bool(options.get("refresh_node"))

        if reset_all:
            if kind == FeatureKind.NODE:
                raise CommandError("--reset-all only supports suite features.")
            deleted_count, fixture_count = reset_all_suite_features()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dropped {deleted_count} suite features before full reload."
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reloaded {fixture_count} mainstream fixtures."
                )
            )


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
            if include_enabled and include_disabled:
                raise CommandError(
                    "Choose only one of --enabled or --disabled when using --feature."
                )
            elif include_enabled:
                _kind = set_feature_enabled(slug=slug, enabled=True, kind=kind)
                _kind, is_enabled = get_feature_state(slug=slug, kind=_kind)
            elif include_disabled:
                _kind = set_feature_enabled(slug=slug, enabled=False, kind=kind)
                _kind, is_enabled = get_feature_state(slug=slug, kind=_kind)
            else:
                _kind, is_enabled = get_feature_state(slug=slug, kind=kind)
            status = "enabled" if is_enabled else "disabled"
            self.stdout.write(f"- {slug} [{status}]")
            return

        status_filter = self._status_filter(
            include_enabled=include_enabled,
            include_disabled=include_disabled,
        )
        kinds = [kind] if kind else [FeatureKind.SUITE, FeatureKind.NODE]

        for index, selected_kind in enumerate(kinds):
            if index:
                self.stdout.write("")
            if selected_kind == FeatureKind.SUITE:
                self._write_rows(
                    heading="Suite features",
                    rows=list_suite_features(enabled=status_filter),
                )
            else:
                self._write_rows(
                    heading="Node features",
                    rows=list_node_features(enabled=status_filter),
                )

    @staticmethod
    def _status_filter(*, include_enabled: bool, include_disabled: bool) -> bool | None:
        """Return status selector used by list helpers.

        ``True`` means enabled only, ``False`` means disabled only, ``None`` means all.
        """

        if include_enabled and include_disabled:
            return None
        if include_disabled:
            return False
        return True

    def _write_rows(self, *, heading: str, rows: list[tuple[str, bool]]) -> None:
        """Render CLI rows for a heading and ``(slug, is_enabled)`` pairs."""

        self.stdout.write(self.style.MIGRATE_HEADING(heading))
        if not rows:
            self.stdout.write("(none)")
            return
        for slug, is_enabled in rows:
            status = "enabled" if is_enabled else "disabled"
            self.stdout.write(f"- {slug} [{status}]")
