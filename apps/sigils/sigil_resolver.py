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

    def __new__(cls, root_name, filter_field, instance_id, key, param):
        return super().__new__(cls, (root_name, filter_field, instance_id, key, param))

    root_name = property(lambda self: self[0])
    filter_field = property(lambda self: self[1])
    instance_id = property(lambda self: self[2])
    key = property(lambda self: self[3])
    param = property(lambda self: self[4])


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
    return name.replace("-", "_")


@lru_cache(maxsize=256)
def _get_sigil_root(prefix: str) -> Optional[SigilRoot]:
    try:
        return SigilRoot.objects.get(prefix__iexact=prefix)
    except SigilRoot.DoesNotExist:
        logger.warning("Unknown sigil root [%s]", prefix)
        return None
    except SigilRoot.MultipleObjectsReturned:
        logger.exception("Multiple sigil roots found [%s]", prefix)
        return None


@lru_cache(maxsize=256)
def _model_field_map(model: type[models.Model]) -> dict[str, models.Field]:
    return {field.name.lower(): field for field in model._meta.fields}


def _candidate_names(name: str) -> list[str]:
    normalized = _normalize_name(name)
    return [
        name,
        normalized,
        normalized.lower(),
        normalized.upper(),
    ]


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


def _parse_token_parts(token: str) -> SigilTokenParts:
    """Parse a sigil token into root, filter field, instance id, key, and param parts.

    Args:
        token: Raw token text without surrounding brackets.

    Returns:
        A tuple-like container with root name, filter field, instance id, key, and param.

    Raises:
        TokenParseError: If the token is missing a root name or contains an incomplete filter.
    """
    i = 0
    n = len(token)
    root_name = ""
    while i < n and token[i] not in ":=.":
        root_name += token[i]
        i += 1
    if not root_name:
        raise TokenParseError("Sigil token is missing a root name")

    filter_field = None
    if i < n and token[i] == ":":
        i += 1
        field = ""
        while i < n and token[i] != "=":
            field += token[i]
            i += 1
        if i == n:
            raise TokenParseError("Sigil token filter is missing an instance identifier")
        filter_field = field.replace("-", "_")

    instance_id = None
    if i < n and token[i] == "=":
        i += 1
        start = i
        depth = 0
        while i < n:
            ch = token[i]
            if ch == "[":
                depth += 1
            elif ch == "]" and depth:
                depth -= 1
            elif ch == "." and depth == 0:
                break
            i += 1
        instance_id = token[start:i]

    key = None
    if i < n and token[i] == ".":
        i += 1
        start = i
        while i < n and token[i] != "=":
            i += 1
        key = token[start:i]

    param = None
    if i < n and token[i] == "=":
        param = token[i + 1 :]

    return SigilTokenParts(root_name, filter_field, instance_id, key, param)


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


def _resolve_config_value(root, normalized_key: str | None, key_upper: str | None, key_lower: str | None, raw_key: str | None, original_token: str) -> str:
    """Resolve configuration-backed sigils, including ENV, CONF, and SYS namespaces."""
    if not normalized_key:
        return ""
    if root.prefix.upper() == "ENV":
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
    if root.prefix.upper() == "CONF":
        for candidate in [normalized_key, key_upper, key_lower]:
            if not candidate:
                continue
            sentinel = object()
            value = getattr(settings, candidate, sentinel)
            if value is not sentinel:
                return str(value)
        return ""
    if root.prefix.upper() == "SYS":
        values = get_system_sigil_values()
        candidates = {
            key_upper,
            normalized_key.upper() if normalized_key else None,
            (raw_key or "").upper(),
        }
        for candidate in candidates:
            if not candidate:
                continue
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
    return _failed_resolution(original_token)


def _resolve_request_root(normalized_key: str | None, raw_key: str | None, param: str | None, param_args: list[str]) -> str:
    """Resolve request-context sigils from the current request."""
    if not normalized_key:
        return ""
    request = get_request()
    param_value = param_args[0] if param_args else (param or "")
    return _resolve_request_value(request, normalized_key or raw_key or "", param_value)


def _resolve_dynamic_root(instance, normalized_key: str | None, key_lower: str | None, raw_key: str | None, param_args: list[str], original_token: str) -> str:
    """Resolve sigils against the dynamic current object."""
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
    """Resolve a sigil against a specific entity instance or serialize that instance."""
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
    """Parse entity aggregate syntax expressed as ``target:function`` in the instance slot."""
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


def _resolve_entity_lookup(model, filter_field: str | None, instance_id: str | None, current: Optional[models.Model]):
    """Resolve the target entity instance from explicit ids or ambient context."""
    instance = None
    if model is None:
        return None

    if instance_id:
        if filter_field:
            field_name = filter_field.lower()
            try:
                field_obj = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                field_obj = None
            if field_obj and isinstance(field_obj, models.CharField):
                lookup = {f"{field_name}__iexact": instance_id}
            else:
                lookup = {field_name: instance_id}
            try:
                instance = model.objects.filter(**lookup).first()
            except (FieldError, TypeError, ValueError):
                instance = None
        else:
            try:
                instance = model.objects.filter(pk=instance_id).first()
            except (TypeError, ValueError):
                instance = None

    if instance is None and instance_id and not filter_field:
        for field in model._meta.fields:
            if field.unique and isinstance(field, models.CharField):
                instance = model.objects.filter(**{f"{field.name}__iexact": instance_id}).first()
                if instance:
                    break

    if instance is None and current and isinstance(current, model):
        instance = current
    if instance is None:
        ctx = get_context()
        inst_pk = ctx.get(model)
        if inst_pk is not None:
            instance = model.objects.filter(pk=inst_pk).first()
    return instance


def _resolve_entity_root(root, filter_field: str | None, instance_id: str | None, normalized_key: str | None, key_lower: str | None, raw_key: str | None, param_args: list[str], current: Optional[models.Model], original_token: str) -> str:
    """Resolve entity-context sigils for model instances, aggregates, and manager dispatch."""
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

    instance = None
    if instance_id or current is not None:
        instance = _resolve_entity_lookup(model, filter_field, instance_id, current)
    else:
        ctx = get_context()
        inst_pk = ctx.get(model)
        if inst_pk is not None:
            instance = model.objects.filter(pk=inst_pk).first()
        if instance is None:
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

    return _failed_resolution(original_token)


def _resolve_token(token: str, current: Optional[models.Model] = None) -> str:
    """Resolve a single sigil token to its string value with graceful degradation."""
    original_token = token
    try:
        token_parts = _parse_token_parts(token)
    except TokenParseError:
        return _failed_resolution(original_token)

    root_name, filter_field, instance_id, key, param = token_parts
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
        ),
    }

    resolver = dispatch.get(root.context_type)
    if resolver is None:
        return _failed_resolution(original_token)

    try:
        return resolver()
    except (AttributeError, FieldDoesNotExist, FieldError, LookupError, TypeError, ValueError):
        logger.exception(
            "Error resolving sigil [%s.%s]",
            lookup_root,
            key_upper or normalized_key or raw_key,
        )
        return _failed_resolution(original_token)


def resolve_sigils(text: str, current: Optional[models.Model] = None) -> str:
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
    return resolve_sigils(sigil, current)
