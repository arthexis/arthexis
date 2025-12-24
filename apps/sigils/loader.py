"""Utilities for hydrating SigilRoot seed data from bundled fixtures."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, OperationalError, connections

from .models import SigilRoot


logger = logging.getLogger(__name__)

FIXTURE_GLOB = "sigil_roots__*.json"


def _iter_fixture_entries(fixtures_dir: Path) -> Iterable[dict]:
    for path in sorted(fixtures_dir.glob(FIXTURE_GLOB)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Failed to read SigilRoot fixture %s: %s", path, exc)
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            fields = entry.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            yield fields


def load_fixture_sigil_roots(sender=None, **kwargs) -> None:
    """Hydrate bundled SigilRoot fixtures while tolerating missing models."""

    del sender

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    using = kwargs.get("using") or SigilRoot.all_objects.db
    manager = _get_sigil_manager(using)

    for fields in _iter_fixture_entries(fixtures_dir):
        prefix = fields.get("prefix")
        context_type = fields.get("context_type")
        if not prefix or not context_type:
            continue

        content_type = fields.get("content_type")
        ct_obj = None
        if isinstance(content_type, (list, tuple)) and len(content_type) == 2:
            app_label, model_name = content_type
            try:
                ct_obj = ContentType.objects.get_by_natural_key(app_label, model_name)
            except ContentType.DoesNotExist:
                logger.debug(
                    "Skipping SigilRoot %s: missing content type %s.%s",
                    prefix,
                    app_label,
                    model_name,
                )
                continue

        _save_sigil_root(
            prefix=prefix,
            defaults={
                "context_type": context_type,
                "content_type": ct_obj,
                "is_seed_data": bool(fields.get("is_seed_data", False)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
            using=using,
            manager=manager,
        )


def _get_sigil_manager(using: str):
    return SigilRoot.all_objects.using(using)


def _save_sigil_root(
    *, prefix: str, defaults: dict, using: str, manager, retries: int = 3
) -> None:
    """Persist a SigilRoot record, retrying on SQLite lock errors."""

    for attempt in range(1, retries + 1):
        try:
            manager.update_or_create(
                prefix=prefix,
                defaults=defaults,
            )
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= retries:
                raise
            logger.warning(
                "Retrying SigilRoot save after database lock (attempt %s/%s)",
                attempt,
                retries,
            )
            manager = _reset_connection(using)
            time.sleep(0.2 * attempt)
        except IntegrityError:
            manager = _reset_connection(using)
            raise


def _reset_connection(using: str):
    connection = connections[using]
    connection.close()
    connection.ensure_connection()
    return _get_sigil_manager(using)
