import ast
import inspect
import textwrap
from datetime import timedelta
import ipaddress
from pathlib import Path

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models import Model
from django.conf import settings
from django.core.cache import cache
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _, ngettext
from core import mailer
from core.models import Lead, RFID, GoogleCalendarProfile
from core.entity import Entity
from ocpp.models import Charger, ChargerConfiguration, CPFirmware
from nodes.models import NetMessage, Node

register = template.Library()

_BADGE_CACHE_TIMEOUT = getattr(settings, "ADMIN_DASHBOARD_BADGE_TIMEOUT", 300)
_BADGE_CACHE_PREFIX = "admin.dashboard.badges"
_CACHE_MISS = object()
_LEAD_OPEN_COUNT_CACHE_PREFIX = f"{_BADGE_CACHE_PREFIX}.lead_open_count"
_RFID_STATS_CACHE_KEY = f"{_BADGE_CACHE_PREFIX}.rfid_release_stats"
_CHARGER_STATS_CACHE_KEY = f"{_BADGE_CACHE_PREFIX}.charger_availability_stats"
_MODEL_RULES_CACHE_KEY = "_model_rule_status_cache"
_NODE_COUNT_CACHE_KEY = f"{_BADGE_CACHE_PREFIX}.node_known_count"
_ALL_RULES_MET_MESSAGE = _("All rules met.")
_SUCCESS_ICON = "✓"
_ERROR_ICON = "✗"


def _format_evcs_list(evcs_identifiers: list[str]) -> str:
    """Return a human-readable list of EVCS identifiers."""

    return ", ".join(evcs_identifiers)


def _rule_success() -> dict[str, object]:
    return {"success": True, "message": _ALL_RULES_MET_MESSAGE, "icon": _SUCCESS_ICON}


def _rule_failure(message: str) -> dict[str, object]:
    return {"success": False, "message": message, "icon": _ERROR_ICON}


def _evaluate_cp_configuration_rules() -> dict[str, object] | None:
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", flat=True)
    )
    charger_ids = [identifier for identifier in chargers if identifier]
    if not charger_ids:
        return _rule_success()

    configured = set(
        ChargerConfiguration.objects.filter(charger_identifier__in=charger_ids)
        .values_list("charger_identifier", flat=True)
    )
    missing = [identifier for identifier in charger_ids if identifier not in configured]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing CP Configuration for %(evcs)s.",
            "Missing CP Configurations for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return _rule_failure(message)

    return _rule_success()


def _evaluate_cp_firmware_rules() -> dict[str, object] | None:
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", flat=True)
    )
    charger_ids = [identifier for identifier in chargers if identifier]
    if not charger_ids:
        return _rule_success()

    firmware_sources = set(
        CPFirmware.objects.filter(
            source_charger__isnull=False,
            source_charger__charger_id__in=charger_ids,
        ).values_list("source_charger__charger_id", flat=True)
    )
    missing = [identifier for identifier in charger_ids if identifier not in firmware_sources]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing CP Firmware for %(evcs)s.",
            "Missing CP Firmware for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return _rule_failure(message)

    return _rule_success()


def _evaluate_evcs_heartbeat_rules() -> dict[str, object] | None:
    cutoff = timezone.now() - timedelta(hours=1)
    chargers = list(
        Charger.objects.filter(connector_id__isnull=True)
        .order_by("charger_id")
        .values_list("charger_id", "last_heartbeat")
    )
    registered = [
        (identifier, heartbeat)
        for identifier, heartbeat in chargers
        if identifier and heartbeat is not None
    ]
    if not registered:
        return _rule_success()

    missing = [
        identifier
        for identifier, heartbeat in registered
        if heartbeat < cutoff
    ]
    if missing:
        evcs_list = _format_evcs_list(missing)
        message = ngettext(
            "Missing EVCS heartbeat within the last hour for %(evcs)s.",
            "Missing EVCS heartbeats within the last hour for %(evcs)s.",
            len(missing),
        ) % {"evcs": evcs_list}
        return _rule_failure(message)

    return _rule_success()


def _evaluate_node_rules() -> dict[str, object]:
    """Return admin dashboard rule metadata for ``nodes.Node``."""

    local_node = Node.get_local()
    if local_node is None:
        return _rule_failure(_("Local node record is missing."))

    if not getattr(local_node, "role_id", None):
        return _rule_failure(_("Local node is missing an assigned role."))

    is_watchtower = (local_node.role.name or "").lower() == "watchtower"
    network_hostname = (local_node.network_hostname or "").strip()
    has_domain_hostname = False
    if network_hostname:
        try:
            ipaddress.ip_address(network_hostname.strip("[]"))
        except ValueError:
            has_domain_hostname = True

    requires_upstream = is_watchtower and has_domain_hostname

    if requires_upstream:
        upstream_nodes = Node.objects.filter(current_relation=Node.Relation.UPSTREAM)
        if not upstream_nodes.exists():
            return _rule_failure(_("At least one upstream node is required."))

        recent_cutoff = timezone.now() - timedelta(hours=24)
        if not upstream_nodes.filter(last_seen__gte=recent_cutoff).exists():
            return _rule_failure(
                _("No upstream nodes have checked in within the last 24 hours."),
            )

    return _rule_success()


_MODEL_RULE_EVALUATORS = {
    "ocpp.Charger": _evaluate_evcs_heartbeat_rules,
    "ocpp.ChargerConfiguration": _evaluate_cp_configuration_rules,
    "ocpp.CPFirmware": _evaluate_cp_firmware_rules,
    "nodes.Node": _evaluate_node_rules,
}


@register.simple_tag
def safe_admin_url(view_name: str, *args, **kwargs) -> str:
    """Reverse an admin URL and gracefully handle missing patterns."""

    try:
        return reverse(view_name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return ""


@register.simple_tag
def admin_can_send_email() -> bool:
    """Return ``True`` when outbound email is configured for this node."""

    return mailer.can_send_email()


def _admin_model_instance(model_admin, request, user):
    model = model_admin.model
    if isinstance(user, model):
        return user
    try:
        queryset = model_admin.get_queryset(request)
    except Exception:
        queryset = model._default_manager.all()
    try:
        return queryset.get(pk=user.pk)
    except model.DoesNotExist:
        return None


def _admin_has_access(model_admin, request, obj):
    if hasattr(model_admin, "has_view_or_change_permission"):
        if model_admin.has_view_or_change_permission(request, obj=obj):
            return True
    else:
        has_change = getattr(model_admin, "has_change_permission", None)
        if has_change and has_change(request, obj):
            return True
        has_view = getattr(model_admin, "has_view_permission", None)
        if has_view and has_view(request, obj):
            return True
    return False


def _admin_change_url(model, user):
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[user.pk])


@register.simple_tag(takes_context=True)
def admin_profile_url(context, user) -> str:
    """Return the first accessible admin change URL for the given user."""

    request = context.get("request")
    if request is None or user is None or not getattr(user, "pk", None):
        return ""

    candidate_models = (
        ("teams", "User"),
        ("core", "User"),
        ("auth", "User"),
    )

    for app_label, model_name in candidate_models:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        model_admin = admin.site._registry.get(model)
        if not model_admin:
            continue

        obj = _admin_model_instance(model_admin, request, user)
        if obj is None:
            continue

        if not _admin_has_access(model_admin, request, obj):
            continue

        try:
            return _admin_change_url(model_admin.model, user)
        except NoReverseMatch:
            continue

    return ""


@register.simple_tag
def last_net_message() -> dict[str, object]:
    """Return the most recent NetMessage for the admin dashboard."""

    try:
        entry = (
            NetMessage.objects.order_by("-created")
            .values("subject", "body")
            .first()
        )
    except DatabaseError:
        return {"text": "", "has_content": False}

    if not entry:
        return {"text": "", "has_content": False}

    subject = (entry.get("subject") or "").strip()
    body = (entry.get("body") or "").strip()
    parts = [part for part in (subject, body) if part]
    text = " — ".join(parts) if parts else ""
    return {"text": text, "has_content": bool(parts)}

@register.simple_tag(takes_context=True)
def model_admin_actions(context, app_label, model_name):
    """Return available admin actions for the given model.

    Only custom actions are returned; the default ``delete_selected`` action is
    ignored. Each action is represented as a dict with ``url`` and ``label``
    keys so templates can render them as links.
    """
    request = context.get("request")
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return []
    model_admin = admin.site._registry.get(model)
    if not model_admin:
        return []

    def uses_queryset(func):
        func = inspect.unwrap(func)
        try:
            source = textwrap.dedent(inspect.getsource(func))
        except (OSError, TypeError):
            return True
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return True
        func_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            None,
        )
        if func_node is None:
            return True

        class Finder(ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_Name(self, node):
                if node.id == "queryset":
                    self.found = True

        finder = Finder()
        for node in func_node.body:
            if finder.found:
                break
            finder.visit(node)
        return finder.found

    actions = []
    seen = set()

    def add_action(action_name, func, label, url):
        if not url:
            return
        actions.append({"url": url, "label": label})
        seen.add(action_name)

    for action_name, (func, _name, description) in model_admin.get_actions(
        request
    ).items():
        if action_name == "delete_selected" or uses_queryset(func):
            continue
        url = None
        label = getattr(
            func,
            "label",
            description or _name.replace("_", " "),
        )
        if action_name == "my_profile":
            getter = getattr(model_admin, "get_my_profile_url", None)
            if callable(getter):
                url = getter(request)
            label_getter = getattr(model_admin, "get_my_profile_label", None)
            if callable(label_getter):
                try:
                    dynamic_label = label_getter(request)
                except Exception:  # pragma: no cover - defensive fallback
                    dynamic_label = None
                if dynamic_label:
                    label = dynamic_label
        base = f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"
        if not url:
            try:
                url = reverse(base + action_name)
            except NoReverseMatch:
                try:
                    url = reverse(base + action_name.split("_")[0])
                except NoReverseMatch:
                    url = reverse(base + "changelist") + f"?action={action_name}"
        add_action(action_name, func, label, url)

    changelist_getter = getattr(model_admin, "get_changelist_actions", None)
    if callable(changelist_getter):
        try:
            changelist_actions = changelist_getter(request)
        except TypeError:
            changelist_actions = changelist_getter()
        for action_name in changelist_actions or []:
            if action_name in seen:
                continue
            func = getattr(model_admin, action_name, None)
            if func is None or uses_queryset(func):
                continue
            label = getattr(
                func,
                "label",
                getattr(
                    func,
                    "short_description",
                    action_name.replace("_", " "),
                ),
            )
            url = None
            tools_view_name = getattr(model_admin, "tools_view_name", None)
            if not tools_view_name:
                initializer = getattr(model_admin, "_get_action_urls", None)
                if callable(initializer):
                    try:
                        initializer()
                    except Exception:  # pragma: no cover - defensive
                        tools_view_name = None
                    else:
                        tools_view_name = getattr(
                            model_admin, "tools_view_name", None
                        )
            if tools_view_name:
                try:
                    url = reverse(tools_view_name, kwargs={"tool": action_name})
                except NoReverseMatch:
                    url = None
            if not url:
                base = f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"
                try:
                    url = reverse(base + action_name)
                except NoReverseMatch:
                    try:
                        url = reverse(base + action_name.split("_")[0])
                    except NoReverseMatch:
                        url = reverse(base + "changelist") + f"?action={action_name}"
            add_action(action_name, func, label, url)

    return actions


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""
    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return ""


@register.simple_tag
def optional_url(viewname: str, *args, **kwargs) -> str:
    """Return ``reverse(viewname)`` or an empty string when missing."""

    try:
        return reverse(viewname, args=args or None, kwargs=kwargs or None)
    except NoReverseMatch:
        return ""


@register.simple_tag
def related_admin_models(opts):
    """Return changelist links for models related to the current model."""

    if not opts:
        return []

    model = getattr(opts, "model", None)
    if model is None:
        return []

    registry = admin.site._registry
    seen = set()
    related = []

    current_labels = {opts.label_lower, model._meta.label_lower}
    concrete = getattr(model._meta, "concrete_model", None)
    if concrete is not None:
        current_labels.add(concrete._meta.label_lower)

    def get_registered(model_cls):
        if not isinstance(model_cls, type) or not issubclass(model_cls, Model):
            return None
        if model_cls in registry:
            return model_cls
        concrete_model = getattr(model_cls, "_meta", None)
        if concrete_model is None:
            return None
        concrete_model = concrete_model.concrete_model
        if concrete_model in registry:
            return concrete_model
        return None

    def describe_relation(field):
        if getattr(field, "one_to_one", False):
            return "1:1", _("One-to-one relationship")
        if getattr(field, "one_to_many", False):
            return "1:N", _("One-to-many relationship")
        if getattr(field, "many_to_one", False):
            return "N:1", _("Many-to-one relationship")
        if getattr(field, "many_to_many", False):
            return "N:N", _("Many-to-many relationship")
        return "—", _("Related model")

    def add_model(model_cls, relation_type: str, relation_title: str):
        registered_model = get_registered(model_cls)
        if registered_model is None:
            return
        model_opts = registered_model._meta
        label_lower = model_opts.label_lower
        if label_lower in current_labels or label_lower in seen:
            return
        concrete_label = model_opts.concrete_model._meta.label_lower
        if concrete_label in current_labels:
            return
        try:
            url = reverse(
                f"admin:{model_opts.app_label}_{model_opts.model_name}_changelist"
            )
        except NoReverseMatch:
            return
        related.append({
            "label": capfirst(model_opts.verbose_name_plural),
            "url": url,
            "relation_type": relation_type,
            "relation_title": relation_title,
        })
        seen.add(label_lower)

    for parent in opts.get_parent_list():
        add_model(parent, "1:1", _("Parent model (multi-table inheritance)"))

    for field in opts.get_fields(include_parents=True, include_hidden=True):
        if not getattr(field, "is_relation", False):
            continue
        related_model = getattr(field, "related_model", None)
        if related_model is None:
            continue
        relation_type, relation_title = describe_relation(field)
        add_model(related_model, relation_type, relation_title)

    related.sort(key=lambda item: item["label"])
    return related


@register.simple_tag(takes_context=True)
def lead_open_count(context, app_label: str, model_name: str):
    """Return the number of open leads for the given model."""

    context_cache = context.setdefault("_lead_open_counts", {})
    cache_key = f"{app_label}.{model_name}".lower()
    if cache_key in context_cache:
        return context_cache[cache_key]

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        context_cache[cache_key] = None
        return None

    concrete = model._meta.concrete_model
    if not issubclass(concrete, Lead):
        context_cache[cache_key] = None
        return None

    concrete_key = concrete._meta.label_lower
    if concrete_key in context_cache:
        count = context_cache[concrete_key]
    else:
        global_cache_key = f"{_LEAD_OPEN_COUNT_CACHE_PREFIX}:{concrete_key}"
        cached_count = cache.get(global_cache_key, _CACHE_MISS)
        if cached_count is _CACHE_MISS:
            try:
                open_value = concrete.Status.OPEN
            except AttributeError:
                cached_count = None
            else:
                cached_count = (
                    concrete._default_manager.filter(status=open_value).count()
                )
            cache.set(global_cache_key, cached_count, _BADGE_CACHE_TIMEOUT)
        count = cached_count
        context_cache[concrete_key] = count

    context_cache[cache_key] = count
    return count


@register.simple_tag(takes_context=True)
def node_known_count(context) -> int | None:
    """Return the number of nodes known to this deployment."""

    context_cache = context.setdefault("_node_known_counts", {})
    cached_count = context_cache.get("count", _CACHE_MISS)
    if cached_count is not _CACHE_MISS:
        return cached_count

    cached_count = cache.get(_NODE_COUNT_CACHE_KEY, _CACHE_MISS)
    if cached_count is _CACHE_MISS:
        try:
            cached_count = Node.objects.count()
        except DatabaseError:
            cached_count = None
        cache.set(_NODE_COUNT_CACHE_KEY, cached_count, _BADGE_CACHE_TIMEOUT)

    context_cache["count"] = cached_count
    return cached_count


@register.simple_tag(takes_context=True)
def rfid_release_stats(context):
    """Return release statistics for the RFID model."""

    cache_key = "_rfid_release_stats"
    stats = context.get(cache_key)
    if stats is None:
        cached_stats = cache.get(_RFID_STATS_CACHE_KEY, _CACHE_MISS)
        if cached_stats is _CACHE_MISS:
            counts = RFID.objects.aggregate(
                total=Count("pk"),
                released_allowed=Count(
                    "pk", filter=Q(released=True, allowed=True)
                ),
            )
            cached_stats = {
                "released_allowed": counts.get("released_allowed") or 0,
                "total": counts.get("total") or 0,
            }
            cache.set(_RFID_STATS_CACHE_KEY, cached_stats, _BADGE_CACHE_TIMEOUT)
        stats = dict(cached_stats)
        context[cache_key] = stats
    return stats


@register.simple_tag(takes_context=True)
def charger_availability_stats(context):
    """Return availability statistics for the Charger model."""

    cache_key = "_charger_availability_stats"
    stats = context.get(cache_key)
    if stats is None:
        cached_stats = cache.get(_CHARGER_STATS_CACHE_KEY, _CACHE_MISS)
        if cached_stats is _CACHE_MISS:
            available = Charger.objects.filter(last_status__iexact="Available")
            available_with_cp_number = available.filter(
                connector_id__isnull=False
            ).count()

            available_without_cp_number = available.filter(
                connector_id__isnull=True
            )
            has_connector = Charger.objects.filter(
                charger_id=OuterRef("charger_id"),
                connector_id__isnull=False,
            )
            missing_connector_count = available_without_cp_number.annotate(
                has_connector=Exists(has_connector)
            ).filter(has_connector=False).count()

            available_total = available_with_cp_number + missing_connector_count
            cached_stats = {
                "available_total": available_total,
                "available_with_cp_number": available_with_cp_number,
                "available_missing_cp_number": missing_connector_count,
            }
            cache.set(
                _CHARGER_STATS_CACHE_KEY, cached_stats, _BADGE_CACHE_TIMEOUT
            )
        stats = dict(cached_stats)
        context[cache_key] = stats
    return stats


@register.simple_tag(takes_context=True)
def model_rule_status(context, app_label: str, model_name: str):
    """Return dashboard rule status metadata for the requested model."""

    cache_map = context.get(_MODEL_RULES_CACHE_KEY)
    if cache_map is None:
        cache_map = {}
        context[_MODEL_RULES_CACHE_KEY] = cache_map

    model_key = f"{app_label}.{model_name}"
    if model_key in cache_map:
        return cache_map[model_key]

    evaluator = _MODEL_RULE_EVALUATORS.get(model_key)
    if evaluator is None:
        result = None
    else:
        result = evaluator()

    cache_map[model_key] = result
    return result


@register.simple_tag(takes_context=True)
def user_google_calendar(context):
    """Return Google Calendar details for the authenticated user."""

    request = context.get("request")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    profile = user.get_profile(GoogleCalendarProfile)
    if not profile:
        return None

    events = profile.fetch_events()
    title = profile.get_display_name() or str(GoogleCalendarProfile._meta.verbose_name)
    calendar_url = profile.build_calendar_url()

    return {
        "title": title,
        "events": events,
        "calendar_url": calendar_url,
        "identifier": profile.resolved_calendar_id(),
        "profile": profile,
    }


@register.simple_tag(takes_context=True)
def celery_feature_enabled(context) -> bool:
    """Return ``True`` when Celery support is enabled for the current node."""

    node = context.get("badge_node")
    if node is not None and hasattr(node, "has_feature"):
        try:
            if node.has_feature("celery-queue"):
                return True
        except Exception:  # pragma: no cover - defensive
            pass

    lock_path = Path(settings.BASE_DIR) / "locks" / "celery.lck"
    return lock_path.exists()


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
def user_data_toggle_url(cl, obj) -> str:
    """Return the admin URL that toggles user datum for ``obj``."""

    if not obj:
        return ""
    try:
        app_label = cl.opts.app_label
        model_name = cl.opts.model_name
    except AttributeError:
        return ""
    try:
        return reverse(
            "admin:user_data_toggle",
            args=(app_label, model_name, obj.pk),
        )
    except NoReverseMatch:
        return ""


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
