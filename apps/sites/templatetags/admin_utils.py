"""Generic admin utility template tags and filters."""

from django import template

from apps.celery.utils import celery_feature_enabled as celery_feature_enabled_helper
from apps.core.entity import Entity

register = template.Library()


@register.simple_tag(takes_context=True)
def celery_feature_enabled(context) -> bool:
    """Return ``True`` when Celery support is enabled for the current node."""

    node = context.get("badge_node")
    return celery_feature_enabled_helper(node)


@register.filter
def supports_user_datum(admin_or_model) -> bool:
    """Return ``True`` when the admin or model supports user datum fixtures."""

    model = getattr(admin_or_model, "model", None) or admin_or_model
    if not isinstance(model, type):
        model = getattr(model, "__class__", None)
    if not isinstance(model, type):
        return False
    if issubclass(model, Entity):
        return True
    return bool(getattr(model, "supports_user_datum", False))


@register.filter
def list_index(sequence, index):
    """Return ``sequence[index]`` while guarding against lookup errors."""

    try:
        position = int(index)
        return sequence[position]
    except (TypeError, ValueError, IndexError):
        return None


@register.simple_tag
def admin_show_filters(cl) -> bool:
    """Return ``True`` when change list filters should be displayed."""

    has_filters = getattr(cl, "has_filters", False)
    if not has_filters:
        return False
    opts = getattr(cl, "opts", None)
    app_label = getattr(opts, "app_label", "") if opts else ""
    model_name = getattr(opts, "model_name", "") if opts else ""
    return not (app_label == "ocpp" and model_name == "charger")
