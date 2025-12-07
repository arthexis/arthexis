from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.template.loader import render_to_string

from .models import Widget, WidgetProfile, WidgetZone
from .registry import WidgetDefinition, get_registered_widget, iter_registered_widgets

logger = logging.getLogger(__name__)


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


def render_zone_widgets(*, request, zone_slug: str, extra_context: dict[str, Any] | None = None) -> list[RenderedWidget]:
    extra_context = extra_context or {}
    sync_registered_widgets()

    widgets = (
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
    widgets = render_zone_widgets(request=request, zone_slug=zone_slug, extra_context=extra_context)
    return "".join(widget.html for widget in widgets)


__all__ = ["RenderedWidget", "render_zone_html", "render_zone_widgets", "sync_registered_widgets"]
