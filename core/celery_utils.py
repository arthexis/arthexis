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


def normalize_periodic_task_name(manager, name: str) -> str:
    """Ensure the stored periodic task name matches the slugified form.

    The helper renames any rows that still use the legacy value so that follow-up
    ``update_or_create`` calls keep working without leaving duplicate tasks in
    the scheduler. When a conflicting slug already exists we keep the slugged
    version and remove the legacy entry.
    """

    slug = slugify_task_name(name)
    if slug == name:
        return slug

    for task in manager.filter(name=name):
        conflict = manager.filter(name=slug).exclude(pk=task.pk).first()
        if conflict:
            # Preserve foreign key references when possible before removing the
            # legacy row.
            related_attr = getattr(task, "client_report_schedule", None)
            if related_attr and getattr(conflict, "client_report_schedule", None) is None:
                related_attr.periodic_task = conflict
                related_attr.save(update_fields=["periodic_task"])
            task.delete()
            continue

        task.name = slug
        try:
            with transaction.atomic():
                task.save(update_fields=["name"])
        except IntegrityError:
            # Another process may have created the slug in between the select and
            # the update. Fall back to deleting the legacy row to avoid duplicate
            # scheduler entries.
            task.refresh_from_db()
            if task.name != slug:
                task.delete()
    return slug
