from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.template.loader import render_to_string

from .models import Widget, WidgetProfile, WidgetZone
from .registry import WidgetDefinition, get_registered_widget, iter_registered_widgets

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30
CACHE_VERSION_KEY = "widgets:zone:{zone_slug}:version"
CACHE_RENDER_KEY = "widgets:zone:{zone_slug}:render:{identity}:{context}:{version}"


@dataclass(slots=True)
class RenderedWidget:
    widget: Widget
    definition: WidgetDefinition
    html: str


def sync_registered_widgets() -> None:
    """Ensure database rows exist for registered widgets and zones."""

    try:
        with transaction.atomic():
            for definition in iter_registered_widgets():
                zone, _ = WidgetZone.objects.get_or_create(
                    slug=definition.zone,
                    defaults={
                        "name": definition.zone_name or definition.zone.title(),
                        "is_seed_data": True,
                    },
                )
                widget, created = Widget.objects.get_or_create(
                    slug=definition.slug,
                    defaults={
                        "name": definition.name,
                        "description": definition.description,
                        "zone": zone,
                        "template_name": definition.template_name,
                        "renderer_path": definition.renderer_path,
                        "priority": definition.order,
                        "is_seed_data": True,
                    },
                )
                updated = False
                for field, value in {
                    "name": definition.name,
                    "description": definition.description,
                    "zone": zone,
                    "template_name": definition.template_name,
                    "renderer_path": definition.renderer_path,
                    "priority": definition.order,
                }.items():
                    if getattr(widget, field) != value:
                        setattr(widget, field, value)
                        updated = True
                if not widget.is_seed_data:
                    widget.is_seed_data = True
                    updated = True
                if updated:
                    widget.save()
    except (OperationalError, ProgrammingError):  # pragma: no cover - database not ready
        logger.debug("Widgets tables unavailable; skipping sync", exc_info=True)


def _build_context(definition: WidgetDefinition, widget: Widget, **kwargs) -> dict[str, Any] | None:
    try:
        context = definition.renderer(widget=widget, **kwargs)
    except Exception:
        logger.exception("Widget renderer failed for %s", definition.slug)
        return None

    if context is None:
        return None

    if not isinstance(context, dict):
        logger.warning("Widget renderer for %s did not return a dict", definition.slug)
        return None

    context.setdefault("widget", widget)
    context.setdefault("definition", definition)
    return context


def _visible(widget: Widget, user) -> bool:
    try:
        return WidgetProfile.visible_for(widget, user)
    except Exception:
        logger.exception("Failed to evaluate widget profile visibility", exc_info=True)
        return False


def _zone_widgets_queryset(zone_slug: str):
    return (
        Widget.objects.select_related("zone")
        .prefetch_related("profiles__user", "profiles__group")
        .filter(
            zone__slug=zone_slug,
            is_enabled=True,
            is_deleted=False,
            zone__is_deleted=False,
        )
        .order_by("priority", "pk")
    )


def render_zone_widgets(*, request, zone_slug: str, extra_context: dict[str, Any] | None = None) -> list[RenderedWidget]:
    extra_context = extra_context or {}

    try:
        widgets = list(_zone_widgets_queryset(zone_slug))
        if not widgets and not Widget.objects.filter(zone__slug=zone_slug).exists():
            sync_registered_widgets()
            widgets = list(_zone_widgets_queryset(zone_slug))
    except (OperationalError, ProgrammingError):  # pragma: no cover - database not ready
        logger.debug("Widgets tables unavailable; skipping render", exc_info=True)
        return []

    rendered: list[RenderedWidget] = []
    for widget in widgets:
        definition = get_registered_widget(widget.slug)
        if definition is None:
            logger.debug("No registered widget definition for %s", widget.slug)
            continue
        if definition.permission and not definition.permission(request=request, widget=widget, **extra_context):
            continue
        if not _visible(widget, getattr(request, "user", None)):
            continue

        context = _build_context(definition, widget, request=request, **extra_context)
        if not context:
            continue
        html = render_to_string(definition.template_name, context=context, request=request)
        rendered.append(RenderedWidget(widget=widget, definition=definition, html=html))

    return rendered


def render_zone_html(*, request, zone_slug: str, extra_context: dict[str, Any] | None = None) -> str:
    extra_context = extra_context or {}
    identity = _cache_identity(getattr(request, "user", None))
    context_key = _cache_context(extra_context)
    version = _zone_cache_version(zone_slug)
    cache_key = CACHE_RENDER_KEY.format(
        zone_slug=zone_slug,
        identity=identity,
        context=context_key,
        version=version,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    widgets = render_zone_widgets(request=request, zone_slug=zone_slug, extra_context=extra_context)
    html = "".join(widget.html for widget in widgets)
    cache.set(cache_key, html, CACHE_TTL_SECONDS)
    return html


def _cache_identity(user) -> str:
    if user and getattr(user, "is_authenticated", False):
        return f"user:{user.id}"
    role = getattr(user, "role", None)
    if role:
        return f"role:{role}"
    return "anonymous"


def _cache_context(extra_context: dict[str, Any]) -> str:
    app = extra_context.get("app") if extra_context else None
    if app is not None:
        app_label = getattr(app, "label", None)
        if app_label:
            return f"app:{app_label}"
        return f"app:{app}"
    return "default"


def _zone_cache_version(zone_slug: str) -> int:
    key = CACHE_VERSION_KEY.format(zone_slug=zone_slug)
    version = cache.get(key)
    if version is None:
        cache.add(key, 1, timeout=None)
        version = cache.get(key, 1)
    return int(version)


def invalidate_zone_cache(zone_slug: str) -> None:
    key = CACHE_VERSION_KEY.format(zone_slug=zone_slug)
    try:
        cache.incr(key)
    except (ValueError, NotImplementedError):
        cache.set(key, int(time.time()))
    logger.debug("Invalidated widget cache for zone %s", zone_slug)


__all__ = [
    "RenderedWidget",
    "invalidate_zone_cache",
    "render_zone_html",
    "render_zone_widgets",
    "sync_registered_widgets",
]
