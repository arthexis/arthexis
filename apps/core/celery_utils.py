"""Utilities for working with Celery periodic task names."""

from __future__ import annotations

import re
from typing import Set

from django.db import transaction
from django.db.utils import IntegrityError


def slugify_task_name(name: str) -> str:
    """Return a slugified task name using dashes.

    Celery stores periodic task names in the database and historically these
    values included underscores or dotted module paths. The scheduler UI reads
    these values directly, so we collapse consecutive underscores or dots into a
    single dash to keep them human readable while remaining unique.
    """

    slug = re.sub(r"[._]+", "-", name)
    # Collapse any accidental duplicate separators that may result from the
    # replacement so ``foo__bar`` and ``foo..bar`` both become ``foo-bar``.
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def periodic_task_name_variants(name: str) -> Set[str]:
    """Return legacy and slugified variants for a periodic task name."""

    slug = slugify_task_name(name)
    if slug == name:
        return {name}
    return {name, slug}


def _reassign_client_report_schedule(source, target) -> None:
    """Move the client report FK to the surviving periodic task if needed."""

    related_attr = getattr(source, "client_report_schedule", None)
    if related_attr and getattr(target, "client_report_schedule", None) is None:
        related_attr.periodic_task = target
        related_attr.save(update_fields=["periodic_task"])


def normalize_periodic_task_name(manager, name: str) -> str:
    """Ensure the stored periodic task name matches the slugified form.

    The helper renames any rows that still use the legacy value so that follow-up
    ``update_or_create`` calls keep working without leaving duplicate tasks in
    the scheduler. When conflicting slug or legacy rows exist, they are
    deduplicated while preserving foreign key relationships where possible.
    """

    slug = slugify_task_name(name)
    variants = periodic_task_name_variants(name)

    # Nothing to normalize when the slug is unchanged and no variants exist.
    if variants == {slug}:
        return slug

    tasks = list(manager.filter(name__in=variants))
    if not tasks:
        return slug

    canonical = next((task for task in tasks if task.name == slug), tasks[0])

    # Drop duplicate rows while preserving relationships.
    for task in tasks:
        if task.pk == canonical.pk:
            continue
        _reassign_client_report_schedule(task, canonical)
        task.delete()

    if canonical.name == slug:
        return slug

    canonical.name = slug
    try:
        with transaction.atomic():
            canonical._core_normalizing = True
            canonical.save(update_fields=["name"])
    except IntegrityError:
        # Another process may have created the slug in between the select and
        # the update. If so, prefer the existing slug and drop the legacy row.
        canonical.refresh_from_db()
        if canonical.name != slug:
            conflict = manager.filter(name=slug).exclude(pk=canonical.pk).first()
            if conflict:
                _reassign_client_report_schedule(canonical, conflict)
                canonical.delete()
                canonical = conflict
    finally:
        if hasattr(canonical, "_core_normalizing"):
            del canonical._core_normalizing

    return canonical.name
