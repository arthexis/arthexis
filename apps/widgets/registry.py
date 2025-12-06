from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

WidgetRenderer = Callable[..., dict[str, Any] | None]
WidgetPermission = Callable[..., bool]


@dataclass(slots=True)
class WidgetDefinition:
    """Metadata for a registered widget callable."""

    slug: str
    name: str
    zone: str
    template_name: str
    renderer: WidgetRenderer
    description: str = ""
    order: int = 0
    zone_name: str | None = None
    permission: WidgetPermission | None = None

    @property
    def renderer_path(self) -> str:
        return f"{self.renderer.__module__}.{self.renderer.__name__}"


_WIDGET_REGISTRY: Dict[str, WidgetDefinition] = {}


def register_widget(
    *,
    slug: str,
    name: str,
    zone: str,
    template_name: str,
    description: str = "",
    order: int = 0,
    zone_name: str | None = None,
    permission: WidgetPermission | None = None,
):
    """Register a widget renderer for later use.

    The decorated callable should accept ``request`` as a keyword argument and may
    return ``None`` to skip rendering.
    """

    def decorator(func: WidgetRenderer) -> WidgetRenderer:
        definition = WidgetDefinition(
            slug=slug,
            name=name,
            zone=zone,
            template_name=template_name,
            renderer=func,
            description=description,
            order=order,
            zone_name=zone_name,
            permission=permission,
        )
        existing = _WIDGET_REGISTRY.get(slug)
        if existing:
            logger.info("Replacing existing widget registration for slug %s", slug)
        _WIDGET_REGISTRY[slug] = definition
        return func

    return decorator


def get_registered_widget(slug: str) -> WidgetDefinition | None:
    return _WIDGET_REGISTRY.get(slug)


def iter_registered_widgets() -> list[WidgetDefinition]:
    return list(_WIDGET_REGISTRY.values())


class WidgetMixin:
    """Class-based helper for defining widgets.

    Subclasses should define the ``widget_*`` attributes and implement
    :meth:`get_widget_context`.
    """

    widget_slug: str
    widget_name: str
    widget_zone: str
    widget_template_name: str
    widget_description: str = ""
    widget_order: int = 0
    widget_zone_name: str | None = None

    @classmethod
    def register(cls, target_cls=None):
        """Decorator that registers a :class:`WidgetMixin` subclass."""

        def _decorator(widget_cls):
            def _renderer(**kwargs):
                instance = widget_cls()
                return instance.get_widget_context(**kwargs)

            register_widget(
                slug=widget_cls.widget_slug,
                name=widget_cls.widget_name,
                zone=widget_cls.widget_zone,
                template_name=widget_cls.widget_template_name,
                description=getattr(widget_cls, "widget_description", ""),
                order=getattr(widget_cls, "widget_order", 0),
                zone_name=getattr(widget_cls, "widget_zone_name", None),
                permission=getattr(widget_cls, "widget_permission", None),
            )(_renderer)
            return widget_cls

        if target_cls is not None:
            return _decorator(target_cls)
        return _decorator

    def get_widget_context(self, **_kwargs):
        raise NotImplementedError


__all__ = [
    "WidgetDefinition",
    "WidgetMixin",
    "register_widget",
    "get_registered_widget",
    "iter_registered_widgets",
]
