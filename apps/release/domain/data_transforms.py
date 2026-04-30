"""Idempotent release data transforms with persisted progress checkpoints."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.db import IntegrityError, connection, transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransformResult:
    """Summary for a single transform execution."""

    updated: int
    processed: int
    complete: bool


TransformRunner = Callable[[dict[str, object]], TransformResult]

_PACKAGE_RELEASE_NORMALIZATION_BATCH_SIZE = 100
_MODULE_PATH_NORMALIZATION_BATCH_SIZE = 100
_OCPP_CHARGING_STATION_LINK_BATCH_SIZE = 200
_OCPP_EXPORT_DEFAULT_BATCH_SIZE = 500
_REPORT_ARCHIVE_BATCH_SIZE = 100
_REPORT_PRODUCT_ARCHIVE_BATCH_SIZE = 100
_VIDEO_DEVICE_NORMALIZATION_BATCH_SIZE = 200
_LOCK_REGISTRY_GUARD = threading.Lock()
_TRANSFORM_LOCKS: dict[str, threading.Lock] = {}


def _database_identity() -> str:
    """Return a stable identity marker for checkpoint compatibility."""

    config = connection.settings_dict
    return "|".join(
        [
            str(connection.vendor),
            str(config.get("NAME") or ""),
            str(config.get("HOST") or ""),
            str(config.get("PORT") or ""),
        ]
    )


def _transform_lock(name: str) -> threading.Lock:
    """Return lock used to serialize execution for a transform name."""

    with _LOCK_REGISTRY_GUARD:
        lock = _TRANSFORM_LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _TRANSFORM_LOCKS[name] = lock
        return lock


def _checkpoint_dir(base_dir: Path | None = None) -> Path:
    """Return the checkpoint directory used by deferred transforms."""

    root = base_dir or Path(settings.BASE_DIR)
    target = root / ".release-transforms"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _load_checkpoint(name: str, *, base_dir: Path | None = None) -> dict[str, object]:
    """Load checkpoint payload for a transform if it exists."""

    path = _checkpoint_dir(base_dir) / f"{name}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid checkpoint payload for transform %s", name)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _store_checkpoint(
    name: str, payload: dict[str, object], *, base_dir: Path | None = None
) -> None:
    """Persist checkpoint payload for a transform."""

    path = _checkpoint_dir(base_dir) / f"{name}.json"
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as tmp_file:
        tmp_file.write(serialized)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
    tmp_path.replace(path)


def _run_package_release_version_normalization(
    checkpoint: dict[str, object],
) -> TransformResult:
    """Normalize package release versions in bounded batches."""

    from apps.release.models import PackageRelease

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    batch_size = _PACKAGE_RELEASE_NORMALIZATION_BATCH_SIZE
    updated = 0
    processed = 0

    queryset = (
        PackageRelease.objects.filter(pk__gt=last_pk)
        .order_by("pk")
        .only("pk", "version")[:batch_size]
    )
    items = list(queryset)
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    for release in items:
        processed += 1
        normalized = PackageRelease.normalize_version(release.version)
        if normalized != release.version:
            release.version = normalized
            try:
                with transaction.atomic():
                    release.save(update_fields=["version"])
            except IntegrityError:
                logger.warning(
                    "Skipping release version normalization due to collision for release pk=%s",
                    release.pk,
                )
            else:
                updated += 1

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(updated=updated, processed=processed, complete=False)


def _run_module_path_normalization(checkpoint: dict[str, object]) -> TransformResult:
    """Normalize legacy module paths in bounded batches."""

    from apps.modules.models import Module

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    items = list(
        Module.objects.filter(pk__gt=last_pk)
        .order_by("pk")
        .only("pk", "path")[:_MODULE_PATH_NORMALIZATION_BATCH_SIZE]
    )
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    updated = 0
    for module in items:
        normalized = Module.normalize_path(module.path)
        if normalized == module.path:
            continue

        target = normalized
        base = (normalized or "/").strip("/")
        suffix_counter = 0
        while (
            Module.objects.exclude(pk=module.pk).filter(path=target).exists()
            or target == module.path
        ) and target is not None:
            suffix = f"{base or 'module'}-{module.pk}"
            if suffix_counter:
                suffix = f"{suffix}-{suffix_counter}"
            target = Module.normalize_path(suffix)
            suffix_counter += 1

        Module.objects.filter(pk=module.pk).update(path=target)
        updated += 1

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(updated=updated, processed=len(items), complete=False)


def _normalized_video_device_slug(name: str) -> str:
    """Return a stable slug for deferred video-device normalization."""

    from django.utils.text import slugify

    slug = slugify(name)
    return slug or uuid.uuid4().hex[:12]


def _run_video_device_name_slug_normalization(
    checkpoint: dict[str, object],
) -> TransformResult:
    """Populate empty legacy video-device names and slugs in batches."""

    from apps.video.models.device import VideoDevice

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    items = list(
        VideoDevice.objects.filter(pk__gt=last_pk)
        .order_by("pk")
        .only("pk", "name", "slug")[:_VIDEO_DEVICE_NORMALIZATION_BATCH_SIZE]
    )
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    updates: list[VideoDevice] = []
    for device in items:
        name = (device.name or "").strip() or VideoDevice.DEFAULT_NAME
        slug = (device.slug or "").strip() or _normalized_video_device_slug(name)
        if device.name == name and device.slug == slug:
            continue
        device.name = name
        device.slug = slug
        updates.append(device)

    if updates:
        VideoDevice.objects.bulk_update(updates, ["name", "slug"], batch_size=200)

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(updated=len(updates), processed=len(items), complete=False)


def _run_video_device_default_name_cleanup(
    checkpoint: dict[str, object],
) -> TransformResult:
    """Rename legacy ``BASE (migrate)`` video-device placeholders in batches."""

    from django.utils.text import slugify

    from apps.video.models.device import VideoDevice

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    old_name = "BASE (migrate)"
    new_name = VideoDevice.DEFAULT_NAME
    old_slug = slugify(old_name)
    new_slug = slugify(new_name)
    items = list(
        VideoDevice.objects.filter(pk__gt=last_pk, name=old_name)
        .order_by("pk")
        .only("pk", "name", "slug")[:_VIDEO_DEVICE_NORMALIZATION_BATCH_SIZE]
    )
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    updates: list[VideoDevice] = []
    for device in items:
        device.name = new_name
        slug = (device.slug or "").strip()
        if not slug or slug == old_slug:
            device.slug = new_slug
        updates.append(device)

    VideoDevice.objects.bulk_update(updates, ["name", "slug"], batch_size=200)
    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(updated=len(updates), processed=len(items), complete=False)


def _run_sql_report_archival(checkpoint: dict[str, object]) -> TransformResult:
    """Archive legacy SQL report definitions in bounded batches."""

    from apps.reports.models import SQLReport

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    raw_cutoff_pk = checkpoint.get("cutoff_pk")
    if raw_cutoff_pk is None:
        cutoff_pk = (
            SQLReport.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        )
        checkpoint["cutoff_pk"] = cutoff_pk
    else:
        try:
            cutoff_pk = int(raw_cutoff_pk)
        except (TypeError, ValueError):
            cutoff_pk = 0
            checkpoint["cutoff_pk"] = cutoff_pk

    items = list(
        SQLReport.objects.filter(pk__gt=last_pk, pk__lte=cutoff_pk).order_by("pk")[
            :_REPORT_ARCHIVE_BATCH_SIZE
        ]
    )
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    reports_to_update: list[SQLReport] = []
    for report in items:
        legacy_definition = {
            "database_alias": report.database_alias or "default",
            "html_template_name": report.html_template_name
            or "reports/sql/default_report.html",
            "query": report.query or "",
        }
        next_values = {
            "legacy_definition": legacy_definition,
            "parameters": {},
            "report_type": SQLReport.ReportType.LEGACY_ARCHIVED,
            "schedule_enabled": False,
            "schedule_interval_minutes": 0,
            "next_scheduled_run_at": None,
        }
        changed = any(
            getattr(report, key) != value for key, value in next_values.items()
        )
        if not changed:
            continue
        for key, value in next_values.items():
            setattr(report, key, value)
        reports_to_update.append(report)

    if reports_to_update:
        SQLReport.objects.bulk_update(
            reports_to_update,
            [
                "legacy_definition",
                "parameters",
                "report_type",
                "schedule_enabled",
                "schedule_interval_minutes",
                "next_scheduled_run_at",
            ],
            batch_size=_REPORT_ARCHIVE_BATCH_SIZE,
        )

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(
        updated=len(reports_to_update), processed=len(items), complete=False
    )


def _run_sql_report_product_archival(checkpoint: dict[str, object]) -> TransformResult:
    """Copy legacy SQL report product metadata into structured fields in batches."""

    from apps.reports.models import SQLReport, SQLReportProduct

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    items = list(
        SQLReportProduct.objects.filter(pk__gt=last_pk)
        .select_related("report")
        .order_by("pk")[:_REPORT_PRODUCT_ARCHIVE_BATCH_SIZE]
    )
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    products_to_update: list[SQLReportProduct] = []
    for product in items:
        report = product.report
        legacy_definition = report.legacy_definition or {}
        next_values = {
            "report_type": report.report_type or SQLReport.ReportType.LEGACY_ARCHIVED,
            "parameters": {},
            "renderer_template_name": legacy_definition.get(
                "html_template_name", "reports/sql/default_report.html"
            ),
            "execution_details": {
                "database_alias": product.database_alias or "default",
                "resolved_sql": product.resolved_sql or "",
            },
        }
        changed = any(
            getattr(product, key) != value for key, value in next_values.items()
        )
        if not changed:
            continue
        for key, value in next_values.items():
            setattr(product, key, value)
        products_to_update.append(product)

    if products_to_update:
        SQLReportProduct.objects.bulk_update(
            products_to_update,
            [
                "report_type",
                "parameters",
                "renderer_template_name",
                "execution_details",
            ],
            batch_size=_REPORT_PRODUCT_ARCHIVE_BATCH_SIZE,
        )

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(
        updated=len(products_to_update), processed=len(items), complete=False
    )


def _run_ocpp_forwarder_default_enablement(
    checkpoint: dict[str, object],
) -> TransformResult:
    """Enable legacy OCPP forwarders and transaction export defaults in batches."""

    from apps.ocpp.models.charger import Charger
    from apps.ocpp.models.cp_forwarder import CPForwarder

    raw_phase = str(checkpoint.get("phase") or "forwarders")
    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    if raw_phase == "forwarders":
        ids = list(
            CPForwarder.objects.filter(pk__gt=last_pk, enabled=False)
            .order_by("pk")
            .values_list("pk", flat=True)[:_OCPP_EXPORT_DEFAULT_BATCH_SIZE]
        )
        if ids:
            updated = CPForwarder.objects.filter(pk__in=ids).update(enabled=True)
            checkpoint["last_pk"] = ids[-1]
            return TransformResult(updated=updated, processed=len(ids), complete=False)
        checkpoint["phase"] = "chargers"
        checkpoint["last_pk"] = 0

    ids = list(
        Charger.objects.filter(
            pk__gt=int(checkpoint.get("last_pk", 0)), export_transactions=False
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:_OCPP_EXPORT_DEFAULT_BATCH_SIZE]
    )
    if not ids:
        return TransformResult(updated=0, processed=0, complete=True)

    updated = Charger.objects.filter(pk__in=ids).update(export_transactions=True)
    checkpoint["last_pk"] = ids[-1]
    checkpoint["phase"] = "chargers"
    return TransformResult(updated=updated, processed=len(ids), complete=False)


def _run_ocpp_charging_station_linking(
    checkpoint: dict[str, object],
) -> TransformResult:
    """Create charging stations and link existing charge points in batches."""

    from apps.ocpp.models.charger import Charger
    from apps.ocpp.models.charging_station import ChargingStation

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    chargers = list(
        Charger.objects.filter(
            pk__gt=last_pk,
            charging_station__isnull=True,
        )
        .exclude(charger_id__isnull=True)
        .exclude(charger_id="")
        .order_by("pk")[:_OCPP_CHARGING_STATION_LINK_BATCH_SIZE]
    )
    if not chargers:
        return TransformResult(updated=0, processed=0, complete=True)

    station_ids = sorted({charger.charger_id for charger in chargers})
    existing = {
        station.station_id: station
        for station in ChargingStation.objects.filter(station_id__in=station_ids)
    }
    to_create = [
        ChargingStation(station_id=station_id)
        for station_id in station_ids
        if station_id not in existing
    ]
    if to_create:
        ChargingStation.objects.bulk_create(to_create, ignore_conflicts=True)
        existing = {
            station.station_id: station
            for station in ChargingStation.objects.filter(station_id__in=station_ids)
        }

    charger_ids_by_station_pk: dict[int, list[int]] = {}
    for charger in chargers:
        station = existing.get(charger.charger_id)
        if station is None:
            continue
        charger_ids_by_station_pk.setdefault(station.pk, []).append(charger.pk)

    updated = 0
    for station_pk, charger_pks in charger_ids_by_station_pk.items():
        updated += Charger.objects.filter(
            pk__in=charger_pks, charging_station__isnull=True
        ).update(charging_station_id=station_pk)

    checkpoint["last_pk"] = chargers[-1].pk
    return TransformResult(updated=updated, processed=len(chargers), complete=False)


def _run_nodes_legacy_cleanup(checkpoint: dict[str, object]) -> TransformResult:
    """Run one batch of the existing deferred node-migration cleanup task."""

    del checkpoint

    from apps.nodes.tasks import (
        DEFERRED_NODE_MIGRATION_BATCH_SIZE,
        run_deferred_node_migrations,
    )

    result = run_deferred_node_migrations(batch_size=DEFERRED_NODE_MIGRATION_BATCH_SIZE)
    processed = int(result.get("role_updates", 0)) + int(
        result.get("node_deletions", 0)
    )
    return TransformResult(
        updated=processed,
        processed=processed,
        complete=bool(result.get("is_complete")),
    )


def _run_agent_skills_filesystem_sync(checkpoint: dict[str, object]) -> TransformResult:
    """Sync skills from database back to SKILL.md files after upgrades."""

    del checkpoint
    from apps.skills.services import sync_db_to_filesystem

    updated = sync_db_to_filesystem()
    return TransformResult(updated=updated, processed=updated, complete=True)


TRANSFORMS: dict[str, TransformRunner] = {
    "skills.sync_filesystem": _run_agent_skills_filesystem_sync,
    "modules.normalize_paths": _run_module_path_normalization,
    "nodes.legacy_data_cleanup": _run_nodes_legacy_cleanup,
    "ocpp.enable_forwarders_and_exports": _run_ocpp_forwarder_default_enablement,
    "ocpp.link_charging_stations": _run_ocpp_charging_station_linking,
    "release.normalize_package_release_versions": _run_package_release_version_normalization,
    "reports.archive_sql_reports": _run_sql_report_archival,
    "reports.archive_sql_report_products": _run_sql_report_product_archival,
    "video.normalize_base_device_name": _run_video_device_default_name_cleanup,
    "video.populate_device_names": _run_video_device_name_slug_normalization,
}


def list_transform_names() -> list[str]:
    """Return registered transform names in deterministic execution order."""

    return list(TRANSFORMS)


def run_transform(name: str, *, base_dir: Path | None = None) -> TransformResult:
    """Execute one transform and persist checkpoint state."""

    runner = TRANSFORMS.get(name)
    if runner is None:
        raise KeyError(f"Unknown release transform: {name}")

    with _transform_lock(name):
        checkpoint = _load_checkpoint(name, base_dir=base_dir)
        identity = _database_identity()
        if checkpoint.get("database_identity") != identity:
            checkpoint = {"database_identity": identity}
        result = runner(checkpoint)
        checkpoint["complete"] = result.complete
        checkpoint["database_identity"] = identity
        _store_checkpoint(name, checkpoint, base_dir=base_dir)
        return result
