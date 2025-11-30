from functools import lru_cache
import logging

from django import template
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.utils.translation import gettext_lazy as _

from apps.counters.dashboard_rules import bind_rule_model, load_callable, rule_failure
from apps.counters.models import BadgeCounter, DashboardRule

register = template.Library()

_BADGE_CACHE_TIMEOUT = getattr(settings, "ADMIN_DASHBOARD_BADGE_TIMEOUT", 300)
_CACHE_MISS = object()
_MODEL_RULES_CACHE_KEY = "_model_rule_status_cache"
_DEFAULT_RULE_HANDLERS = {
    "ocpp.charger": "evaluate_evcs_heartbeat_rules",
    "ocpp.chargerconfiguration": "evaluate_cp_configuration_rules",
    "ocpp.cpfirmware": "evaluate_cp_firmware_rules",
    "nodes.node": "evaluate_node_rules",
    "teams.emailinbox": "evaluate_email_profile_rules",
    "teams.emailoutbox": "evaluate_email_profile_rules",
}

logger = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def _get_content_type(app_label: str, model_name: str) -> ContentType | None:
    try:
        return ContentType.objects.get(app_label=app_label, model=model_name)
    except ContentType.DoesNotExist:
        return None


def get_cached_content_type(app_label: str, model_name: str) -> ContentType | None:
    """Return a cached content type lookup for the provided model key."""

    return _get_content_type(app_label, model_name.lower())


@lru_cache(maxsize=256)
def _get_dashboard_rule(app_label: str, model_name: str) -> DashboardRule | None:
    content_type = get_cached_content_type(app_label, model_name)
    if content_type is None:
        return None

    return (
        DashboardRule.objects.select_related("content_type")
        .filter(content_type=content_type)
        .first()
    )


def get_cached_dashboard_rule(app_label: str, model_name: str) -> DashboardRule | None:
    """Return a cached dashboard rule for the provided model key."""

    return _get_dashboard_rule(app_label, model_name.lower())


def _clear_dashboard_rule_caches(**_kwargs):
    _get_content_type.cache_clear()
    _get_dashboard_rule.cache_clear()


post_save.connect(_clear_dashboard_rule_caches, sender=DashboardRule)
post_delete.connect(_clear_dashboard_rule_caches, sender=DashboardRule)
post_save.connect(_clear_dashboard_rule_caches, sender=ContentType)
post_delete.connect(_clear_dashboard_rule_caches, sender=ContentType)


@register.simple_tag(takes_context=True)
def badge_counters(context, app_label: str, model_name: str) -> list[dict[str, object]]:
    """Return cached badge counters for the requested model."""

    cache_map = context.setdefault("_badge_counters", {})
    cache_key = f"{app_label}.{model_name}".lower()
    if cache_key in cache_map:
        return cache_map[cache_key]

    content_type = get_cached_content_type(app_label, model_name)
    if content_type is None:
        counters: list[dict[str, object]] = []
        cache_map[cache_key] = counters
        return counters

    global_cache_key = BadgeCounter.cache_key_for_content_type(content_type.pk)
    cached_counters = cache.get(global_cache_key, _CACHE_MISS)
    if cached_counters is _CACHE_MISS:
        counters: list[dict[str, object]] = []
        queryset = BadgeCounter.objects.filter(
            content_type=content_type, is_enabled=True
        ).order_by("priority", "pk")
        for counter in queryset:
            display = counter.build_display()
            if display:
                counters.append(display)
        cache.set(global_cache_key, counters, _BADGE_CACHE_TIMEOUT)
    else:
        counters = cached_counters

    cache_map[cache_key] = counters
    return counters


@register.simple_tag(takes_context=True)
def model_rule_status(context, app_label: str, model_name: str):
    """Return dashboard rule status metadata for the requested model."""

    cache_map = context.get(_MODEL_RULES_CACHE_KEY)
    if cache_map is None:
        cache_map = {}
        context[_MODEL_RULES_CACHE_KEY] = cache_map

    lookup_key = f"{app_label}.{model_name}"
    normalized_key = lookup_key.lower()
    if normalized_key in cache_map:
        return cache_map[normalized_key]

    rule = get_cached_dashboard_rule(app_label, model_name)

    if rule is None:
        handler_name = _DEFAULT_RULE_HANDLERS.get(normalized_key)
        handler = load_callable(handler_name) if handler_name else None
        if handler:
            try:
                with bind_rule_model(normalized_key):
                    result = handler()
            except Exception:
                logger.exception(
                    "Dashboard rule handler failed", extra={"model": normalized_key}
                )
                result = rule_failure(_("Unable to evaluate dashboard rule."))
        else:
            result = None
    else:
        try:
            with bind_rule_model(normalized_key):
                result = rule.evaluate()
        except Exception:
            logger.exception(
                "Dashboard rule evaluation failed",
                extra={"model": normalized_key, "rule_id": rule.pk},
            )
            result = rule_failure(_("Unable to evaluate dashboard rule."))

    cache_map[normalized_key] = result
    return result
