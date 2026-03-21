import atexit
import concurrent.futures
import json
import logging
import os
from decimal import Decimal
from functools import lru_cache
from typing import Iterable, Optional

from django.conf import settings
from django.core import serializers
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import models
from django.db.models import Count, Max, Min, Sum

from .models import SigilRoot
from .scanner import scan_sigil_tokens
from .sigil_context import get_context, get_request
from .system import get_system_sigil_values, resolve_system_namespace_value

logger = logging.getLogger(__name__)

ATTRIBUTE_RESOLUTION_TIMEOUT = float(os.environ.get("SIGIL_ATTRIBUTE_TIMEOUT", 2.0))
ATTRIBUTE_RESOLUTION_WORKERS = int(os.environ.get("SIGIL_ATTRIBUTE_WORKERS", 4)) or 1
_ATTRIBUTE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=ATTRIBUTE_RESOLUTION_WORKERS,
    thread_name_prefix="sigil-attr",
)


class TokenParseError(ValueError):
    """Raised when a sigil token cannot be parsed into resolver parts."""


class SigilTokenParts(tuple):
    """Tuple-like container for parsed sigil token pieces."""

    __slots__ = ()

    def __new__(cls, root_name, filter_field, instance_id, key, param, strict_key):
        return super().__new__(cls, (root_name, filter_field, instance_id, key, param, strict_key))

    root_name = property(lambda self: self[0])
    filter_field = property(lambda self: self[1])
    instance_id = property(lambda self: self[2])
    key = property(lambda self: self[3])
    param = property(lambda self: self[4])
    strict_key = property(lambda self: self[5])


def _shutdown_attribute_executor():
    _ATTRIBUTE_EXECUTOR.shutdown(wait=True, cancel_futures=True)


atexit.register(_shutdown_attribute_executor)


def _first_instance(model: type[models.Model]) -> Optional[models.Model]:
    """Return the first model instance honoring model ordering when available."""
    qs = model.objects
    ordering = list(getattr(model._meta, "ordering", []))
    if ordering:
        qs = qs.order_by(*ordering)
    else:
        qs = qs.order_by("?")
    return qs.first()


def _failed_resolution(token: str) -> str:
    return f"[{token}]"


def _normalize_name(name: str) -> str:
    """Normalize sigil identifier names so hyphens and underscores are interchangeable."""
    return name.replace("-", "_")


def _identifier_variants(name: str | None) -> list[str]:
    """Return normalized sigil identifier variants for tolerant lookups.

    Args:
        name: Raw sigil identifier text.

    Returns:
        Ordered unique variants that treat hyphens and underscores as equivalent.
    """
    if not name:
        return []
    normalized = _normalize_name(name)
    dashed = normalized.replace("_", "-")
    variants: list[str] = []
    for candidate in (name, normalized, dashed, normalized.lower(), normalized.upper(), dashed.lower(), dashed.upper()):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


@lru_cache(maxsize=256)
def _get_sigil_root(prefix: str) -> Optional[SigilRoot]:
    """Fetch a sigil root while treating hyphens and underscores as equivalent."""
    for candidate in _identifier_variants(prefix):
        try:
            return SigilRoot.objects.get(prefix__iexact=candidate)
        except SigilRoot.DoesNotExist:
            continue
        except SigilRoot.MultipleObjectsReturned:
            logger.exception("Multiple sigil roots found [%s]", candidate)
            return None
    logger.warning("Unknown sigil root [%s]", prefix)
    return None


@lru_cache(maxsize=256)
def _model_field_map(model: type[models.Model]) -> dict[str, models.Field]:
    """Return a case-insensitive mapping of model field names to fields.

    Args:
        model: Django model class to introspect.

    Returns:
        A dictionary keyed by lowercase field name.
    """
    return {field.name.lower(): field for field in model._meta.fields}


@lru_cache(maxsize=256)
def _unique_char_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Return unique character field names for fallback entity lookups.

    Args:
        model: Django model class to introspect.

    Returns:
        A tuple of unique ``CharField`` names that support case-insensitive fallback lookup.
    """
    return tuple(
        field.name
        for field in model._meta.fields
        if field.unique and isinstance(field, models.CharField)
    )


def _candidate_names(name: str) -> list[str]:
    return _identifier_variants(name)


def _stringify_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is None:
        return ""
    return str(value)


def _coerce_numeric(value):
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _call_attribute(obj, name: str, args: list[str]):
    for candidate in _candidate_names(name):
        if not hasattr(obj, candidate):
            continue
        attr = getattr(obj, candidate)
        if callable(attr):
            if ATTRIBUTE_RESOLUTION_TIMEOUT and ATTRIBUTE_RESOLUTION_TIMEOUT > 0:
                future = _ATTRIBUTE_EXECUTOR.submit(attr, *args)
                try:
                    return True, future.result(timeout=ATTRIBUTE_RESOLUTION_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    logger.warning(
                        "Sigil attribute %s.%s exceeded timeout (%ss)",
                        obj.__class__.__name__,
                        candidate,
                        ATTRIBUTE_RESOLUTION_TIMEOUT,
                    )
                    raise TimeoutError(
                        f"Sigil attribute {obj.__class__.__name__}.{candidate} exceeded timeout"
                    )
                except TypeError:
                    return True, None
            try:
                return True, attr(*args)
            except TypeError:
                return True, None
        return True, attr
    return False, None


def _aggregate_values(values: Iterable[float], func: str) -> Optional[str]:
    """Aggregate numeric values using the requested aggregate function."""
    collected = [v for v in values if v is not None]
    if func == "count":
        return str(len(collected))
    if not collected:
        return ""
    if func == "min":
        return str(min(collected))
    if func == "max":
        return str(max(collected))
    return str(sum(collected))


def _parse_root_name(token: str, index: int) -> tuple[str, int]:
    """Parse the sigil root name from the current token offset.

    Args:
        token: Raw token text without surrounding brackets.
        index: Starting offset within ``token``.

    Returns:
        A tuple containing the parsed root name and the next unread offset.

    Raises:
        TokenParseError: If the token does not start with a root name.
    """
    start = index
    while index < len(token):
        if token[index] in ":=." or token[index : index + 2] == "->":
            break
        index += 1
    root_name = token[start:index]
    if not root_name:
        raise TokenParseError("Sigil token is missing a root name")
    return root_name, index


def _parse_filter_field(token: str, index: int) -> tuple[str | None, int]:
    """Parse an optional ``:filter`` segment from a sigil token.

    Args:
        token: Raw token text without surrounding brackets.
        index: Current offset immediately after the root name.

    Returns:
        A tuple containing the normalized filter field, if present, and the next unread offset.

    Raises:
        TokenParseError: If a filter segment is started but no instance identifier follows it.
    """
    if index >= len(token) or token[index] != ":":
        return None, index

    index += 1
    start = index
    while index < len(token) and token[index] != "=":
        index += 1
    if index == len(token):
        raise TokenParseError("Sigil token filter is missing an instance identifier")
    return token[start:index].replace("-", "_"), index


def _parse_instance_id(token: str, index: int) -> tuple[str | None, int]:
    """Parse an optional ``=instance`` segment, honoring nested sigils.

    Args:
        token: Raw token text without surrounding brackets.
        index: Current offset after any filter segment.

    Returns:
        A tuple containing the parsed instance identifier, if present, and the next unread offset.

    Raises:
        TokenParseError: This helper does not currently raise parsing errors.
    """
    if index >= len(token) or token[index] != "=":
        return None, index

    index += 1
    return _parse_quoted_segment(
        token,
        index,
        lambda value, offset, depth, in_quotes: depth == 0
        and not in_quotes
        and (value[offset] == "." or value[offset : offset + 2] == "->"),
    )


def _parse_quoted_segment(
    token: str,
    index: int,
    should_stop,
) -> tuple[str, int]:
    """Parse a token segment while honoring nested sigils and quoted text.

    Args:
        token: Raw token text without surrounding brackets.
        index: Current offset at the segment start.
        should_stop: Callable that returns ``True`` when the current position ends the segment.

    Returns:
        The parsed segment value with surrounding double quotes removed, and the next unread offset.
    """
    start = index
    depth = 0
    in_quotes = False
    quoted = False
    while index < len(token):
        if should_stop(token, index, depth, in_quotes):
            break
        char = token[index]
        if char == "\\" and in_quotes and index + 1 < len(token):
            index += 2
            continue
        if char == '"':
            if index == start:
                quoted = True
            in_quotes = not in_quotes
            index += 1
            continue
        if not in_quotes:
            if char == "[":
                depth += 1
            elif char == "]" and depth:
                depth -= 1
        index += 1

    value = token[start:index]
    if quoted and len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value, index


def _parse_key_segment(token: str, index: int) -> tuple[str | None, bool, int]:
    """Parse an optional ``.key`` or ``->key`` segment.

    Args:
        token: Raw token text without surrounding brackets.
        index: Current offset after any instance selector.

    Returns:
        A tuple containing the parsed key, strict-key flag, and the next unread offset.
    """
    strict_key = False
    key_started = False
    if index < len(token) and token[index] == ".":
        index += 1
        key_started = True
    elif token[index : index + 2] == "->":
        strict_key = True
        index += 2
        key_started = True

    if not key_started:
        return None, strict_key, index

    key, index = _parse_quoted_segment(
        token,
        index,
        lambda value, offset, depth, in_quotes: depth == 0 and not in_quotes and value[offset] == "=",
    )
    return key, strict_key, index


def _parse_key_and_param(token: str, index: int) -> tuple[str | None, str | None, bool, int]:
    """Parse optional ``.key``/``->key`` and ``=param`` segments from a sigil token.

    Args:
        token: Raw token text without surrounding brackets.
        index: Current offset after any instance identifier.

    Returns:
        A tuple containing the key, parameter, strict-key flag, and next unread offset.

    Raises:
        TokenParseError: This helper does not currently raise parsing errors.
    """
    key, strict_key, index = _parse_key_segment(token, index)

    param = None
    if index < len(token) and token[index] == "=":
        param = token[index + 1 :]
        index = len(token)

    return key, param, strict_key, index


def _parse_token_parts(token: str) -> SigilTokenParts:
    """Parse a sigil token into normalized resolver parts.

    Args:
        token: Raw token text without surrounding brackets.

    Returns:
        Parsed root, filter field, instance identifier, key, and parameter values.

    Raises:
        TokenParseError: If the token is malformed and cannot be decomposed.
    """
    root_name, index = _parse_root_name(token, 0)
    filter_field, index = _parse_filter_field(token, index)
    instance_id, index = _parse_instance_id(token, index)
    key, param, strict_key, index = _parse_key_and_param(token, index)
    return SigilTokenParts(root_name, filter_field, instance_id, key, param, strict_key)


def _resolve_request_value(request, key: str, param: str) -> str:
    """Resolve request-bound sigil values from the active request object."""
    if request is None or not key:
        return ""
    key = key.lower()
    if key == "method":
        return request.method
    if key == "path":
        return request.path
    if key == "full_path":
        return request.get_full_path()
    if key == "scheme":
        return request.scheme
    if key == "host":
        return request.get_host()
    if key in {"url", "absolute_uri"}:
        return request.build_absolute_uri()
    if key == "query_string":
        return request.META.get("QUERY_STRING", "")
    if key in {"ip", "remote_addr"}:
        return request.META.get("REMOTE_ADDR", "")
    if key == "user":
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            return str(user)
        return ""
    if key in {"header", "headers"}:
        if not param:
            return ""
        return request.headers.get(param, "")
    if key == "meta":
        if not param:
            return ""
        return str(request.META.get(param, ""))
    if key in {"query", "get", "param"}:
        if not param:
            return ""
        return request.GET.get(param, "")
    if key == "post":
        if not param:
            return ""
        return request.POST.get(param, "")
    if key in {"cookie", "cookies"}:
        if not param:
            return ""
        return request.COOKIES.get(param, "")
    return ""


def _resolve_instance_value(instance, model, key_name: str, key_lower: str, raw_key: str, param_args: list[str]):
    """Resolve an instance value via custom resolver, model field, or callable attribute.

    Args:
        instance: Model instance being resolved.
        model: Model class for the instance.
        key_name: Normalized key name used for attribute lookup.
        key_lower: Lowercase key used for model field lookup.
        raw_key: Original key text from the token.
        param_args: Positional arguments for callable attributes.

    Returns:
        A resolved string value, or ``None`` when no resolution path succeeds.
    """
    resolver = getattr(instance, "resolve_profile_field_value", None)
    if callable(resolver):
        try:
            handled, custom_value = resolver(key_name or raw_key or "")
        except TypeError:
            handled = False
            custom_value = None
        if handled:
            return _stringify_value(custom_value)

    field = _model_field_map(model).get(key_lower or "")
    if field:
        value = getattr(instance, field.attname)
        if isinstance(field, models.ForeignKey):
            related = getattr(instance, field.name, None)
            if related is not None:
                value = related
        return _stringify_value(value)

    found, attr_val = _call_attribute(instance, key_name or raw_key or "", param_args)
    if found:
        return _stringify_value(attr_val)
    return None


def _resolve_env_value(normalized_key: str | None, key_upper: str | None, key_lower: str | None, raw_key: str | None, original_token: str) -> str:
    """Resolve an ``ENV`` sigil from process environment variables.

    Args:
        normalized_key: Hyphen-normalized token key.
        key_upper: Uppercase normalized key.
        key_lower: Lowercase normalized key.
        raw_key: Original token key text.
        original_token: Token used for degraded failure output.

    Returns:
        The resolved environment variable value or the degraded placeholder.
    """
    candidates = []
    if raw_key:
        candidates.append(raw_key.replace("-", "_"))
    if normalized_key:
        candidates.append(normalized_key)
    if key_upper:
        candidates.append(key_upper)
    if key_lower:
        candidates.append(key_lower)
    seen_candidates: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        value = os.environ.get(candidate)
        if value is not None:
            return value
    logger.warning(
        "Missing environment variable for sigil [ENV.%s]",
        key_upper or normalized_key or raw_key or "",
    )
    return _failed_resolution(original_token)


def _resolve_conf_value(normalized_key: str | None, key_upper: str | None, key_lower: str | None) -> str:
    """Resolve a ``CONF`` sigil from Django settings.

    Args:
        normalized_key: Hyphen-normalized token key.
        key_upper: Uppercase normalized key.
        key_lower: Lowercase normalized key.

    Returns:
        The resolved setting value as a string, or an empty string when absent.
    """
    for candidate in [normalized_key, key_upper, key_lower]:
        if not candidate:
            continue
        sentinel = object()
        value = getattr(settings, candidate, sentinel)
        if value is not sentinel:
            return str(value)
    return ""


def _resolve_sys_value(normalized_key: str | None, key_upper: str | None, raw_key: str | None, original_token: str) -> str:
    """Resolve a ``SYS`` sigil from cached or computed system metadata.

    Args:
        normalized_key: Hyphen-normalized token key.
        key_upper: Uppercase normalized key.
        raw_key: Original token key text.
        original_token: Token used for degraded failure output.

    Returns:
        The resolved system value or the degraded placeholder.
    """
    values = get_system_sigil_values()
    candidates = [candidate for candidate in [key_upper, (raw_key or "").upper()] if candidate]
    for candidate in candidates:
        if candidate in values:
            return values[candidate]
        resolved = resolve_system_namespace_value(candidate)
        if resolved is not None:
            return resolved
    logger.warning(
        "Missing system information for sigil [SYS.%s]",
        key_upper or normalized_key or raw_key or "",
    )
    return _failed_resolution(original_token)


def _resolve_config_value(root, normalized_key: str | None, key_upper: str | None, key_lower: str | None, raw_key: str | None, original_token: str) -> str:
    """Resolve configuration-backed sigils, including ENV, CONF, and SYS namespaces.

    Args:
        root: Matching ``SigilRoot`` configuration.
        normalized_key: Hyphen-normalized token key.
        key_upper: Uppercase normalized key.
        key_lower: Lowercase normalized key.
        raw_key: Original token key text.
        original_token: Token used for degraded failure output.

    Returns:
        The resolved configuration value, an empty string for optional misses, or the degraded placeholder.
    """
    if not normalized_key:
        return ""
    prefix = root.prefix.upper()
    if prefix == "ENV":
        return _resolve_env_value(normalized_key, key_upper, key_lower, raw_key, original_token)
    if prefix == "CONF":
        return _resolve_conf_value(normalized_key, key_upper, key_lower)
    if prefix == "SYS":
        return _resolve_sys_value(normalized_key, key_upper, raw_key, original_token)
    return _failed_resolution(original_token)


def _resolve_request_root(normalized_key: str | None, raw_key: str | None, param: str | None, param_args: list[str]) -> str:
    """Resolve request-context sigils from the current request.

    Args:
        normalized_key: Hyphen-normalized token key.
        raw_key: Original token key text.
        param: Raw parameter text from the token.
        param_args: Resolved parameter arguments for callable-style request lookups.

    Returns:
        The resolved request-derived value as a string.
    """
    if not normalized_key:
        return ""
    request = get_request()
    param_value = param_args[0] if param_args else (param or "")
    return _resolve_request_value(request, normalized_key or raw_key or "", param_value)


def _resolve_dynamic_root(
    instance,
    normalized_key: str | None,
    key_lower: str | None,
    raw_key: str | None,
    param_args: list[str],
    original_token: str,
) -> str:
    """Resolve sigils against the dynamic current object.

    Args:
        instance: Current model instance supplied to resolution.
        normalized_key: Hyphen-normalized token key.
        key_lower: Lowercase normalized key.
        raw_key: Original token key text.
        param_args: Resolved positional arguments for callable attributes.
        original_token: Token used for degraded failure output.

    Returns:
        A resolved string value or serialized instance payload.
    """
    if normalized_key:
        resolved = _resolve_instance_value(
            instance,
            instance.__class__,
            normalized_key,
            key_lower or "",
            raw_key or "",
            param_args,
        )
        if resolved is not None:
            return resolved
        return _failed_resolution(original_token)
    return serializers.serialize("json", [instance])


def _resolve_entity_instance(model, instance, normalized_key: str | None, key_lower: str | None, raw_key: str | None, param_args: list[str], original_token: str) -> str:
    """Resolve a sigil against a specific entity instance or serialize that instance.

    Args:
        model: Django model class for the instance.
        instance: Concrete model instance being resolved.
        normalized_key: Hyphen-normalized token key.
        key_lower: Lowercase normalized key.
        raw_key: Original token key text.
        param_args: Resolved positional arguments for callable attributes.
        original_token: Token used for degraded failure output.

    Returns:
        A resolved string value or serialized instance payload.
    """
    if normalized_key:
        resolved = _resolve_instance_value(
            instance,
            model,
            normalized_key,
            key_lower or "",
            raw_key or "",
            param_args,
        )
        if resolved is not None:
            return resolved
        return _failed_resolution(original_token)
    return serializers.serialize("json", [instance])


def _parse_aggregate_request(filter_field: str | None, instance_id: str | None, normalized_key: str | None):
    """Parse entity aggregate syntax expressed as ``target:function`` in the instance slot.

    Args:
        filter_field: Optional named lookup field from the token.
        instance_id: Optional instance selector that may encode an aggregate request.
        normalized_key: Hyphen-normalized token key.

    Returns:
        A tuple of aggregate target and aggregate function, or ``(None, None)`` when not applicable.
    """
    if filter_field or instance_id is None or normalized_key is not None or ":" not in instance_id:
        return None, None
    aggregate_target, aggregate_func = instance_id.split(":", 1)
    return aggregate_target, _normalize_name(aggregate_func or "total").lower()


def _resolve_entity_aggregate(model, aggregate_target: str | None, aggregate_func: str | None, param_args: list[str], original_token: str) -> str | None:
    """Resolve entity aggregate syntax for model-backed sigils.

    Args:
        model: Model class to aggregate.
        aggregate_target: Field or attribute to aggregate.
        aggregate_func: Aggregate function name.
        param_args: Positional arguments for callable aggregate targets.
        original_token: Token used for degraded failure output.

    Returns:
        Aggregated string result, or ``None`` when the token is not an aggregate request.
    """
    aggregate_candidates = {"total", "count", "min", "max"}
    if aggregate_func not in aggregate_candidates:
        return None

    qs = model.objects.all()
    target_name = _normalize_name(aggregate_target or "")
    if aggregate_func == "count" and not target_name:
        return str(qs.count())

    field = _model_field_map(model).get(target_name.lower()) if target_name else None
    if field and aggregate_func in {"total", "min", "max", "count"}:
        aggregation_map = {
            "count": Count,
            "max": Max,
            "min": Min,
            "total": Sum,
        }
        agg_class = aggregation_map.get(aggregate_func)
        if agg_class:
            result = qs.aggregate(value=agg_class(field.attname)).get("value")
            return "" if result is None else str(result)

    values: list[float] = []
    for obj in qs:
        source = None
        if target_name:
            if field:
                source = getattr(obj, field.attname)
            else:
                found, source = _call_attribute(obj, target_name, param_args)
                if not found:
                    continue
        if source is None:
            continue
        numeric = _coerce_numeric(source)
        if numeric is not None:
            values.append(numeric)

    aggregated = _aggregate_values(values, aggregate_func)
    return aggregated if aggregated is not None else _failed_resolution(original_token)


def _resolve_entity_lookup(
    model,
    filter_field: str | None,
    instance_id: str | None,
    current: Optional[models.Model],
) -> tuple[Optional[models.Model], bool]:
    """Resolve the target entity instance from explicit selectors or ambient context.

    Args:
        model: Target Django model class.
        filter_field: Optional named field used for lookup.
        instance_id: Optional explicit instance selector from the token.
        current: Optional ambient current model instance.

    Returns:
        A tuple containing a matching model instance, if any, and a flag indicating
        whether the lookup itself was invalid.

    Raises:
        FieldError: Caught internally when malformed lookups reach the ORM.
    """
    instance = None
    invalid_lookup = False
    if model is None:
        return None, False

    if instance_id is not None:
        if filter_field:
            field_name = filter_field.lower()
            try:
                field_obj = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                invalid_lookup = True
                field_obj = None
            if field_obj and isinstance(field_obj, models.CharField):
                lookup = {f"{field_name}__iexact": instance_id}
            else:
                lookup = {field_name: instance_id}
            try:
                instance = model.objects.filter(**lookup).first()
            except (FieldError, TypeError, ValueError):
                invalid_lookup = True
                instance = None
        else:
            try:
                instance = model.objects.filter(pk=instance_id).first()
            except (TypeError, ValueError):
                instance = None

    if instance is None and instance_id is not None and not filter_field:
        for field_name in _unique_char_fields(model):
            instance = model.objects.filter(**{f"{field_name}__iexact": instance_id}).first()
            if instance:
                break

    if instance is None and instance_id is None and current and isinstance(current, model):
        instance = current
    if instance is None and instance_id is None:
        ctx = get_context()
        inst_pk = ctx.get(model)
        if inst_pk is not None:
            instance = model.objects.filter(pk=inst_pk).first()
    return instance, invalid_lookup


def _resolve_entity_root(
    root,
    filter_field: str | None,
    instance_id: str | None,
    normalized_key: str | None,
    key_lower: str | None,
    raw_key: str | None,
    param_args: list[str],
    current: Optional[models.Model],
    original_token: str,
    strict_key: bool,
) -> str:
    """Resolve entity-context sigils for model instances, aggregates, and manager dispatch.

    Args:
        root: Matching ``SigilRoot`` configuration.
        filter_field: Optional named field used for lookup.
        instance_id: Optional explicit instance selector from the token.
        normalized_key: Hyphen-normalized token key.
        key_lower: Lowercase normalized key.
        raw_key: Original token key text.
        param_args: Resolved positional arguments for callable attributes.
        current: Optional ambient current model instance.
        original_token: Token used for degraded failure output.
        strict_key: Whether key access used ``->`` strict semantics.

    Returns:
        The resolved entity value, serialized data, or degraded placeholder.
    """
    model = root.content_type.model_class() if root.content_type else None
    if model is None:
        return _failed_resolution(original_token)

    aggregate_target, aggregate_func = _parse_aggregate_request(filter_field, instance_id, normalized_key)
    if aggregate_func is not None:
        aggregate_result = _resolve_entity_aggregate(
            model,
            aggregate_target,
            aggregate_func,
            param_args,
            original_token,
        )
        if aggregate_result is not None:
            return aggregate_result

    instance, invalid_lookup = _resolve_entity_lookup(model, filter_field, instance_id, current)
    if instance is None and instance_id is None:
        instance = root.default_instance()

    if instance:
        return _resolve_entity_instance(
            model,
            instance,
            normalized_key,
            key_lower,
            raw_key,
            param_args,
            original_token,
        )

    manager_method_name = instance_id if not filter_field and normalized_key is None else None
    if manager_method_name:
        found, manager_val = _call_attribute(model.objects, manager_method_name, param_args)
        if found:
            if isinstance(manager_val, models.QuerySet):
                return serializers.serialize("json", manager_val)
            if isinstance(manager_val, models.Model):
                return serializers.serialize("json", [manager_val])
            return _stringify_value(manager_val)

    if invalid_lookup:
        return _failed_resolution(original_token)
    if normalized_key and not strict_key:
        return ""
    return _failed_resolution(original_token)


def _resolve_token(token: str, current: Optional[models.Model] = None) -> str:
    """Resolve a single sigil token to its string value with graceful degradation."""
    original_token = token
    try:
        token_parts = _parse_token_parts(token)
    except TokenParseError:
        return _failed_resolution(original_token)

    root_name, filter_field, instance_id, key, param, strict_key = token_parts
    normalized_root = _normalize_name(root_name)
    lookup_root = normalized_root.upper()
    raw_key = key
    normalized_key = _normalize_name(key) if key else None
    key_upper = normalized_key.upper() if normalized_key else None
    key_lower = normalized_key.lower() if normalized_key else None

    param_args: list[str] = []
    if param:
        param = resolve_sigils(param, current)
        if param:
            param_args = param.split(",")
    if instance_id:
        instance_id = resolve_sigils(instance_id, current)

    if lookup_root == "OBJECT" and current is not None:
        return _resolve_dynamic_root(
            current,
            normalized_key,
            key_lower,
            raw_key,
            param_args,
            original_token,
        )

    root = _get_sigil_root(lookup_root)
    if root is None:
        return _failed_resolution(original_token)

    dispatch = {
        SigilRoot.Context.CONFIG: lambda: _resolve_config_value(
            root,
            normalized_key,
            key_upper,
            key_lower,
            raw_key,
            original_token,
        ),
        SigilRoot.Context.REQUEST: lambda: _resolve_request_root(
            normalized_key,
            raw_key,
            param,
            param_args,
        ),
        SigilRoot.Context.ENTITY: lambda: _resolve_entity_root(
            root,
            filter_field,
            instance_id,
            normalized_key,
            key_lower,
            raw_key,
            param_args,
            current,
            original_token,
            strict_key,
        ),
    }

    resolver = dispatch.get(root.context_type)
    if resolver is None:
        return _failed_resolution(original_token)

    try:
        return resolver()
    except (
        AttributeError,
        FieldDoesNotExist,
        FieldError,
        LookupError,
        TimeoutError,
        TypeError,
        ValueError,
    ):
        logger.exception(
            "Error resolving sigil [%s.%s]",
            lookup_root,
            key_upper or normalized_key or raw_key,
        )
        return _failed_resolution(original_token)


def resolve_sigils(text: str, current: Optional[models.Model] = None) -> str:
    """Resolve every sigil token found in the given text.

    Args:
        text: Source text that may contain bracketed sigil tokens.
        current: Optional current model instance used for OBJECT and entity resolution.

    Returns:
        The input text with each recognized sigil token replaced by its resolved value.
    """
    parts: list[str] = []
    cursor = 0
    for span in scan_sigil_tokens(text):
        if span.start > cursor:
            parts.append(text[cursor:span.start])
        token = text[span.start + 1 : span.end - 1]
        parts.append(_resolve_token(token, current))
        cursor = span.end
    if cursor < len(text):
        parts.append(text[cursor:])
    return "".join(parts)


def resolve_sigil(sigil: str, current: Optional[models.Model] = None) -> str:
    """Resolve a single sigil-bearing string.

    Args:
        sigil: Text containing one or more sigil tokens.
        current: Optional current model instance used during resolution.

    Returns:
        The resolved sigil text.
    """
    return resolve_sigils(sigil, current)
