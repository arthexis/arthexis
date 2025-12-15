from __future__ import annotations

from pathlib import Path
import hashlib
import logging

from django.conf import settings

from .classifiers import run_default_classifiers, suppress_default_classifiers
from .models import ContentSample

logger = logging.getLogger(__name__)


def save_content_sample(
    *,
    path: Path,
    kind: str,
    node=None,
    method: str = "",
    transaction_uuid=None,
    user=None,
    link_duplicates: bool = False,
    content: str | None = None,
    duplicate_log_context: str,
):
    """Persist a :class:`ContentSample` if an identical hash is not present."""

    original = path
    if not path.is_absolute():
        path = settings.LOG_DIR / path
    with path.open("rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    existing = ContentSample.objects.filter(hash=digest).first()
    if existing:
        if link_duplicates:
            logger.info("Duplicate %s; reusing existing sample", duplicate_log_context)
            return existing
        logger.info("Duplicate %s; record not created", duplicate_log_context)
        return None
    stored_path = (original if not original.is_absolute() else path).as_posix()
    data = {
        "node": node,
        "path": stored_path,
        "method": method,
        "hash": digest,
        "kind": kind,
    }
    if transaction_uuid is not None:
        data["transaction_uuid"] = transaction_uuid
    if content is not None:
        data["content"] = content
    if user is not None:
        data["user"] = user
    with suppress_default_classifiers():
        sample = ContentSample.objects.create(**data)
    run_default_classifiers(sample)
    return sample
