"""Runtime registry and execution helpers for :mod:`content` classifiers."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from functools import lru_cache
from importlib import import_module
from typing import Any, Iterable, Iterator, Mapping

from django.db import transaction
from django.utils.text import slugify

from apps.content.models import (
    ContentClassification,
    ContentClassifier,
    ContentSample,
    ContentTag,
)

logger = logging.getLogger(__name__)


_thread_state = threading.local()


def should_skip_default_classifiers() -> bool:
    """Return ``True`` when classifier execution is temporarily suppressed."""

    return bool(getattr(_thread_state, "suppress", 0))


@contextmanager
def suppress_default_classifiers() -> Iterator[None]:
    """Context manager that suspends automatic classifier execution."""

    depth = getattr(_thread_state, "suppress", 0)
    _thread_state.suppress = depth + 1
    try:
        yield
    finally:
        if depth:
            _thread_state.suppress = depth
        else:
            _thread_state.suppress = 0


@lru_cache(maxsize=None)
def _resolve_entrypoint(entrypoint: str):
    module_path, _, attr = entrypoint.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid classifier entrypoint '{entrypoint}'")
    module = import_module(module_path)
    try:
        func = getattr(module, attr)
    except AttributeError as exc:  # pragma: no cover - defensive guard
        raise ImportError(f"Entrypoint '{entrypoint}' not found") from exc
    if not callable(func):
        raise TypeError(f"Entrypoint '{entrypoint}' is not callable")
    return func


def _coerce_metadata(metadata: Any) -> Any:
    if metadata is None:
        return None
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return metadata


def _ensure_tag(result: Any) -> tuple[ContentTag, float | None, Any]:
    confidence: float | None = None
    metadata: Any = None
    tag: ContentTag | None = None

    if isinstance(result, ContentTag):
        tag = result
    elif isinstance(result, str):
        slug = slugify(result)
        label = result if result.strip() else slug
        tag, _ = ContentTag.objects.get_or_create(
            slug=slug or "untitled", defaults={"label": label or "Untitled"}
        )
    elif isinstance(result, Mapping):
        slug_value = result.get("slug") or result.get("label")
        if not slug_value:
            raise ValueError("Classifier result mapping must include 'slug' or 'label'")
        slug = slugify(str(slug_value))
        label = result.get("label") or slug_value
        tag, _ = ContentTag.objects.get_or_create(
            slug=slug, defaults={"label": str(label)}
        )
        confidence_value = result.get("confidence")
        if confidence_value is not None:
            confidence = float(confidence_value)
        metadata = _coerce_metadata(result.get("metadata"))
    elif isinstance(result, Iterable):
        seq = list(result)
        if not seq:
            raise ValueError("Empty classifier result")
        tag_candidate = seq[0]
        tag, confidence, metadata = _ensure_tag(tag_candidate)
        if len(seq) > 1 and confidence is None and seq[1] is not None:
            confidence = float(seq[1])
        if len(seq) > 2 and metadata is None:
            metadata = _coerce_metadata(seq[2])
    else:
        raise TypeError(f"Unsupported classifier result type: {type(result)!r}")

    if tag is None:
        raise ValueError("Classifier result did not produce a tag")

    return tag, confidence, metadata


def run_classifier(
    classifier: ContentClassifier, sample: ContentSample
) -> list[ContentClassification]:
    """Execute a single classifier for ``sample`` and persist the results."""

    if classifier.kind != sample.kind:
        logger.debug(
            "Skipping classifier %s for %s: incompatible kind", classifier.slug, sample.pk
        )
        return []

    if not classifier.active:
        logger.debug("Skipping classifier %s: inactive", classifier.slug)
        return []

    try:
        callable_obj = _resolve_entrypoint(classifier.entrypoint)
    except Exception:  # pragma: no cover - exercised in logging path
        logger.exception("Unable to resolve classifier entrypoint %s", classifier.entrypoint)
        return []

    try:
        results = callable_obj(sample)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception(
            "Classifier %s raised an error while processing sample %s",
            classifier.slug,
            sample.pk,
        )
        return []

    if not results:
        return []

    classifications: list[ContentClassification] = []

    with transaction.atomic():
        for item in results:
            try:
                tag, confidence, metadata = _ensure_tag(item)
            except Exception:
                logger.exception(
                    "Classifier %s returned invalid result %r", classifier.slug, item
                )
                continue

            defaults: dict[str, Any] = {}
            if confidence is not None:
                defaults["confidence"] = confidence
            if metadata is not None:
                defaults["metadata"] = metadata

            classification, _ = ContentClassification.objects.update_or_create(
                sample=sample,
                classifier=classifier,
                tag=tag,
                defaults=defaults,
            )
            classifications.append(classification)

    return classifications


def run_default_classifiers(sample: ContentSample) -> list[ContentClassification]:
    """Run all classifiers marked to execute by default for ``sample``."""

    classifiers = ContentClassifier.objects.filter(
        active=True, run_by_default=True, kind=sample.kind
    )
    results: list[ContentClassification] = []
    for classifier in classifiers:
        results.extend(run_classifier(classifier, sample))
    return results
