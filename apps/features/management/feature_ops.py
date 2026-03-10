from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.app.models import Application
from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@dataclass(frozen=True)
class FeatureKind:
    """Constants for supported feature kinds."""

    SUITE: str = "suite"
    NODE: str = "node"


def infer_kind_for_slug(*, slug: str, kind: str | None = None) -> str:
    """Return the feature kind for ``slug``.

    Raises:
        CommandError: If the slug is unknown or ambiguous across feature catalogs.
    """

    if kind is not None:
        if kind not in (FeatureKind.SUITE, FeatureKind.NODE):
            raise CommandError(f"Unsupported feature kind: {kind}")
        return kind
    suite_exists = Feature.objects.filter(slug=slug).exists()
    node_exists = NodeFeature.objects.filter(slug=slug).exists()
    if suite_exists and node_exists:
        raise CommandError(
            f"Feature '{slug}' exists in both suite and node kinds; specify --kind."
        )
    if suite_exists:
        return FeatureKind.SUITE
    if node_exists:
        return FeatureKind.NODE
    raise CommandError(
        f"Unknown feature '{slug}'. Specify --kind to target a specific feature catalog."
    )


def set_feature_enabled(*, slug: str, enabled: bool, kind: str | None = None) -> str:
    """Set the enabled state for a single feature and return its resolved kind."""

    resolved_kind = infer_kind_for_slug(slug=slug, kind=kind)
    if resolved_kind == FeatureKind.SUITE:
        feature = Feature.objects.filter(slug=slug).first()
        if feature is None:
            raise CommandError(f"Unknown suite feature: {slug}")
        feature.set_enabled(enabled)
        return resolved_kind

    if resolved_kind == FeatureKind.NODE:
        feature = NodeFeature.objects.filter(slug=slug).first()
        if feature is None:
            raise CommandError(f"Unknown node feature: {slug}")
        node = Node.get_local()
        if node is None:
            raise CommandError(
                "No local node is registered for node feature operations."
            )
        if enabled:
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
        else:
            NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        return resolved_kind

    raise CommandError(f"Unsupported feature kind: {resolved_kind}")


def get_feature_state(*, slug: str, kind: str | None = None) -> tuple[str, bool]:
    """Return ``(resolved_kind, is_enabled)`` for a single feature slug."""

    resolved_kind = infer_kind_for_slug(slug=slug, kind=kind)
    if resolved_kind == FeatureKind.SUITE:
        feature = Feature.objects.filter(slug=slug).first()
        if feature is None:
            raise CommandError(f"Unknown suite feature: {slug}")
        return resolved_kind, bool(feature.is_enabled)

    node_feature = NodeFeature.objects.filter(slug=slug).first()
    if node_feature is None:
        raise CommandError(f"Unknown node feature: {slug}")
    node = Node.get_local()
    if node is None:
        return resolved_kind, False
    enabled = NodeFeatureAssignment.objects.filter(
        node=node, feature=node_feature
    ).exists()
    return resolved_kind, enabled


def list_suite_features(*, enabled: bool | None = True) -> list[tuple[str, bool]]:
    """Return suite feature rows as ``(slug, is_enabled)`` pairs."""

    queryset = Feature.objects.order_by("slug")
    if enabled is True:
        queryset = queryset.filter(is_enabled=True)
    elif enabled is False:
        queryset = queryset.filter(is_enabled=False)
    return [(feature.slug, bool(feature.is_enabled)) for feature in queryset]


def list_node_features(*, enabled: bool | None = True) -> list[tuple[str, bool]]:
    """Return node feature rows as ``(slug, is_enabled)`` pairs."""

    node = Node.get_local()
    assigned = set(node.features.values_list("slug", flat=True)) if node else set()
    queryset = NodeFeature.objects.order_by("slug")
    if enabled is True:
        queryset = queryset.filter(slug__in=assigned)
    elif enabled is False:
        queryset = queryset.exclude(slug__in=assigned)
    return [(feature.slug, feature.slug in assigned) for feature in queryset]


def refresh_local_node_features() -> Node | None:
    """Refresh auto-managed feature assignments for the local node.

    Returns:
        Node | None: The local node when present, otherwise ``None``.
    """

    node = Node.get_local()
    if node is None:
        return None
    node.refresh_features()
    return node


def refresh_and_report_local_node_features(command: BaseCommand) -> Node | None:
    """Refresh local-node features and write consistent CLI output."""

    node = refresh_local_node_features()
    if node is None:
        command.stdout.write(
            command.style.WARNING("Local node not found, skipping feature refresh.")
        )
        return None

    command.stdout.write(f"Refreshing features for local node {node}...")
    command.stdout.write(command.style.SUCCESS("Successfully refreshed features."))
    return node


def _ensure_fixture_applications_exist(*, fixture_paths: list[Path]) -> None:
    """Ensure fixture-referenced ``Application`` rows exist before fixture loading."""

    labels: set[str] = set()
    for fixture_path in fixture_paths:
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except OSError:
            continue

        if not isinstance(payload, list):
            continue

        for item in payload:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            main_app = fields.get("main_app")
            if isinstance(main_app, list) and main_app:
                main_app = main_app[0]
            if isinstance(main_app, str) and main_app.strip():
                labels.add(main_app.strip())

    for label in sorted(labels):
        Application.objects.get_or_create(name=label)


def _ensure_reset_baseline_features() -> None:
    """Restore baseline suite features that are seeded by migrations, not fixtures."""

    Feature.objects.update_or_create(
        slug="development-blog",
        defaults={
            "display": "Development Blog",
            "is_enabled": True,
        },
    )


def reset_all_suite_features() -> tuple[int, int]:
    """Reload mainstream suite feature fixtures.

    Returns:
        tuple[int, int]: ``(deleted_count, fixture_count)``.

    Raises:
        CommandError: If no mainstream fixtures are available.
    """

    fixtures_dir = Path(settings.BASE_DIR) / "apps" / "features" / "fixtures"
    fixture_paths = sorted(fixtures_dir.glob("features__*.json"))
    if not fixture_paths:
        raise CommandError("No feature fixtures found.")

    feature_manager = getattr(Feature, "all_objects", Feature._default_manager)
    deleted_count = feature_manager.filter(is_deleted=False).count()
    with transaction.atomic():
        feature_manager.update(is_seed_data=False, is_enabled=False)
        feature_manager.all().delete()
        _ensure_fixture_applications_exist(fixture_paths=fixture_paths)
        call_command(
            "loaddata", *(str(path) for path in fixture_paths), verbosity=0
        )
        _ensure_reset_baseline_features()
    return deleted_count, len(fixture_paths)
