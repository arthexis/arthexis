import atexit
import concurrent.futures
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.core import serializers
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import models

from . import entity_lookup
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


@dataclass(frozen=True)
class ResolutionContext:
    """Token resolution context shared across resolver strategies."""

    current: models.Model | None
    instance_id: str | None
    key_lower: str | None
    key_upper: str | None
    lookup_root: str
    normalized_key: str | None
    original_token: str
    param: str | None
    param_args: tuple[str, ...]
    raw_key: str | None
    token_parts: SigilTokenParts


def _shutdown_attribute_executor():
    _ATTRIBUTE_EXECUTOR.shutdown(wait=True, cancel_futures=True)


atexit.register(_shutdown_attribute_executor)


def _first_instance(model: type[models.Model]) -> models.Model | None:
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
def _get_sigil_root(prefix: str) -> SigilRoot | None:
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


def _candidate_names(name: str) -> list[str]:
    return _identifier_variants(name)


def _stringify_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is None:
        return ""
    return str(value)


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
                    ) from None
                except TypeError:
                    return True, None
            try:
                return True, attr(*args)
            except TypeError:
                return True, None
        return True, attr
    return False, None


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

    field = entity_lookup.model_field_map(model).get(key_lower or "")
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


def _resolve_entity_root(
    root: SigilRoot,
    context: ResolutionContext,
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
        return _failed_resolution(context.original_token)

    aggregate_target, aggregate_func = entity_lookup.parse_aggregate_request(
        context.token_parts.filter_field,
        context.instance_id,
        context.normalized_key,
    )
    if aggregate_func is not None:
        aggregate_result = entity_lookup.resolve_entity_aggregate(
            model,
            aggregate_target,
            aggregate_func,
            list(context.param_args),
            _call_attribute,
        )
        if aggregate_result is not None:
            return aggregate_result

    instance, invalid_lookup = entity_lookup.resolve_entity_lookup(
        model,
        context.token_parts.filter_field,
        context.instance_id,
        context.current,
    )
    if instance is None and context.instance_id is None:
        ctx = get_context()
        inst_pk = ctx.get(model)
        if inst_pk is not None:
            instance = model.objects.filter(pk=inst_pk).first()
    if instance is None and context.instance_id is None:
        instance = root.default_instance()

    if instance:
        return _resolve_entity_instance(
            model,
            instance,
            context.normalized_key,
            context.key_lower,
            context.raw_key,
            list(context.param_args),
            context.original_token,
        )

    manager_method_name = (
        context.instance_id
        if not context.token_parts.filter_field and context.normalized_key is None
        else None
    )
    if manager_method_name:
        found, manager_val = _call_attribute(model.objects, manager_method_name, list(context.param_args))
        if found:
            if isinstance(manager_val, models.QuerySet):
                return serializers.serialize("json", manager_val)
            if isinstance(manager_val, models.Model):
                return serializers.serialize("json", [manager_val])
            return _stringify_value(manager_val)

    if invalid_lookup:
        return _failed_resolution(context.original_token)
    if context.normalized_key and not context.token_parts.strict_key:
        return ""
    return _failed_resolution(context.original_token)


def _build_resolution_context(
    token: str,
    current: models.Model | None,
) -> ResolutionContext:
    """Build normalized token resolution context from a raw token."""
    token_parts = _parse_token_parts(token)
    normalized_root = _normalize_name(token_parts.root_name)
    lookup_root = normalized_root.upper()
    normalized_key = _normalize_name(token_parts.key) if token_parts.key else None
    key_upper = normalized_key.upper() if normalized_key else None
    key_lower = normalized_key.lower() if normalized_key else None
    param = resolve_sigils(token_parts.param, current) if token_parts.param else token_parts.param
    param_args = tuple(param.split(",")) if param else ()
    instance_id = resolve_sigils(token_parts.instance_id, current) if token_parts.instance_id else token_parts.instance_id
    return ResolutionContext(
        current=current,
        instance_id=instance_id,
        key_lower=key_lower,
        key_upper=key_upper,
        lookup_root=lookup_root,
        normalized_key=normalized_key,
        original_token=token,
        param=param,
        param_args=param_args,
        raw_key=token_parts.key,
        token_parts=token_parts,
    )


def _resolve_context_config(root: SigilRoot, context: ResolutionContext) -> str:
    return _resolve_config_value(
        root,
        context.normalized_key,
        context.key_upper,
        context.key_lower,
        context.raw_key,
        context.original_token,
    )


def _resolve_context_request(_root: SigilRoot, context: ResolutionContext) -> str:
    return _resolve_request_root(
        context.normalized_key,
        context.raw_key,
        context.param,
        list(context.param_args),
    )


def _resolve_context_entity(root: SigilRoot, context: ResolutionContext) -> str:
    return _resolve_entity_root(root, context)


CONTEXT_RESOLVERS = {
    SigilRoot.Context.CONFIG: _resolve_context_config,
    SigilRoot.Context.REQUEST: _resolve_context_request,
    SigilRoot.Context.ENTITY: _resolve_context_entity,
}


def _resolve_token(token: str, current: models.Model | None = None) -> str:
    """Resolve a single sigil token to its string value with graceful degradation."""
    try:
        context = _build_resolution_context(token, current)
    except TokenParseError:
        return _failed_resolution(token)

    if context.lookup_root == "OBJECT" and context.current is not None:
        return _resolve_dynamic_root(
            context.current,
            context.normalized_key,
            context.key_lower,
            context.raw_key,
            list(context.param_args),
            context.original_token,
        )

    root = _get_sigil_root(context.lookup_root)
    if root is None:
        return _failed_resolution(context.original_token)

    resolver = CONTEXT_RESOLVERS.get(root.context_type)
    if resolver is None:
        return _failed_resolution(context.original_token)

    try:
        return resolver(root, context)
    except TimeoutError:
        logger.warning(
            "Timed out resolving sigil [%s.%s]",
            context.lookup_root,
            context.key_upper or context.normalized_key or context.raw_key,
        )
        return _failed_resolution(context.original_token)
    except (
        AttributeError,
        FieldDoesNotExist,
        FieldError,
        LookupError,
        TypeError,
        ValueError,
    ):
        logger.exception(
            "Error resolving sigil [%s.%s]",
            context.lookup_root,
            context.key_upper or context.normalized_key or context.raw_key,
        )
        return _failed_resolution(context.original_token)


def resolve_sigils(text: str, current: models.Model | None = None) -> str:
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


def resolve_sigil(sigil: str, current: models.Model | None = None) -> str:
    """Resolve a single sigil-bearing string.

    Args:
        sigil: Text containing one or more sigil tokens.
        current: Optional current model instance used during resolution.

    Returns:
        The resolved sigil text.
    """
    return resolve_sigils(sigil, current)
