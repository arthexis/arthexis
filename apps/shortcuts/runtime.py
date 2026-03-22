"""Runtime helpers for executing shortcuts and auto-enabling listener features."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from django.core.exceptions import ValidationError

from apps.features.models import Feature
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.feature_detection import is_feature_active_for_node
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.sigils.sigil_resolver import resolve_sigils

from .constants import SHORTCUT_LISTENER_NODE_FEATURE_SLUG, SHORTCUT_MANAGEMENT_FEATURE_SLUG
from .models import ClipboardPattern, Shortcut
from .utils import resolve_arg_tokens


class ShortcutExecutionError(RuntimeError):
    """Raised when a shortcut target cannot be executed."""


@dataclass(frozen=True)
class ActionResult:
    """Typed result returned by a shortcut action executor."""

    identifier: str
    value: Any
    payload: dict[str, Any]


@dataclass(frozen=True)
class ShortcutExecution:
    """Structured shortcut execution output."""

    target_kind: str
    target_identifier: str
    action_result: ActionResult
    clipboard_output: str
    keyboard_output: str
    matched_pattern_id: int | None


def render_shortcut_output(template: str, *, context: dict[str, Any]) -> str:
    """Render output text by combining sigils and argument placeholders."""

    if not template:
        return ""
    rendered = resolve_sigils(template, current=None)
    return resolve_arg_tokens(rendered, (), context)


def _select_pattern(shortcut: Shortcut, clipboard: str) -> ClipboardPattern | None:
    """Return the first active pattern that matches ``clipboard`` text."""

    if not shortcut.use_clipboard_patterns:
        return None

    for pattern in shortcut.clipboard_patterns.filter(is_active=True).order_by("priority", "pk"):
        if re.search(pattern.pattern, clipboard or ""):
            return pattern
    return None


def _normalize_payload(value: object) -> dict[str, Any]:
    """Return a predictable JSON-object payload for target execution."""

    return value if isinstance(value, dict) else {}


def _read_source_value(source: str, context: dict[str, Any]) -> str:
    """Return a string value from the allowed runtime context sources."""

    mapping = {
        "clipboard": str(context.get("clipboard") or ""),
        "keyboard": str(context.get("keyboard") or ""),
        "shortcut_key": str(context.get("shortcut_key") or ""),
    }
    if source not in mapping:
        raise ShortcutExecutionError(f"Unsupported text source: {source}")
    return mapping[source]


def execute_shortcut_target(*, kind: str, identifier: str, payload: object, context: dict[str, Any]) -> ActionResult:
    """Execute a non-programmable shortcut target.

    Parameters:
        kind: Target category (action, command, workflow).
        identifier: Structured target identifier.
        payload: Target parameters persisted on the shortcut record.
        context: Runtime context from the shortcut invocation.

    Returns:
        ActionResult: Structured action output.

    Raises:
        ShortcutExecutionError: If the target definition is unsupported or invalid.
    """

    normalized_payload = _normalize_payload(payload)

    # Use string literals here so runtime behavior remains stable during migrations.
    if kind == "action":
        if identifier == "clipboard.echo":
            value = _read_source_value("clipboard", context)
        elif identifier == "keyboard.echo":
            value = _read_source_value("keyboard", context)
        elif identifier == "text.static":
            value = str(normalized_payload.get("text") or "")
        else:
            raise ShortcutExecutionError(f"Unsupported shortcut action: {identifier}")
        return ActionResult(identifier=identifier, value=value, payload={"value": value})

    if kind == "command":
        source = str(normalized_payload.get("source") or "clipboard")
        base_value = _read_source_value(source, context)
        if identifier == "text.append_suffix":
            suffix = str(normalized_payload.get("suffix") or "")
            value = f"{base_value}{suffix}"
        elif identifier == "text.prepend_prefix":
            prefix = str(normalized_payload.get("prefix") or "")
            value = f"{prefix}{base_value}"
        elif identifier == "text.replace":
            old = str(normalized_payload.get("old") or "")
            new = str(normalized_payload.get("new") or "")
            value = base_value.replace(old, new)
        else:
            raise ShortcutExecutionError(f"Unsupported shortcut command: {identifier}")
        return ActionResult(identifier=identifier, value=value, payload={"value": value, "source": source})

    if kind == "workflow":
        if identifier != "text.render_template":
            raise ShortcutExecutionError(f"Unsupported shortcut workflow: {identifier}")
        template = str(normalized_payload.get("template") or "")
        rendered = render_shortcut_output(template, context=context)
        return ActionResult(
            identifier=identifier,
            value=rendered,
            payload={"value": rendered, "template": template},
        )

    raise ShortcutExecutionError(f"Unsupported shortcut target kind: {kind}")


def execute_client_shortcut(*, shortcut: Shortcut, clipboard: str, keyboard: str = "") -> ShortcutExecution:
    """Execute a client shortcut and return typed/clipboard output payload."""

    if shortcut.kind != Shortcut.Kind.CLIENT:
        raise ValidationError("Only client shortcuts can execute through browser endpoint.")

    selected_pattern = _select_pattern(shortcut, clipboard)
    target = selected_pattern or shortcut
    shortcut.validate_target_fields(
        kind=target.target_kind,
        identifier=target.target_identifier,
        payload=target.target_payload,
    )
    context = {
        "clipboard": clipboard,
        "keyboard": keyboard,
        "shortcut_key": shortcut.key_combo,
    }
    action_result = execute_shortcut_target(
        kind=target.target_kind,
        identifier=target.target_identifier,
        payload=target.target_payload,
        context=context,
    )
    context["action_result"] = action_result.value
    context["action_payload"] = action_result.payload
    context["recipe_result"] = action_result.value
    output_template = (getattr(target, "output_template", "") or "").strip()
    rendered_output = render_shortcut_output(output_template, context=context)
    fallback_output = str(action_result.value) if action_result.value is not None else ""
    value = rendered_output or fallback_output

    clipboard_enabled = bool(getattr(target, "clipboard_output_enabled", False))
    keyboard_enabled = bool(getattr(target, "keyboard_output_enabled", False))
    return ShortcutExecution(
        target_kind=target.target_kind,
        target_identifier=target.target_identifier,
        action_result=action_result,
        clipboard_output=value if clipboard_enabled else "",
        keyboard_output=value if keyboard_enabled else "",
        matched_pattern_id=getattr(selected_pattern, "pk", None),
    )


def execute_server_shortcut(*, shortcut: Shortcut) -> ShortcutExecution:
    """Execute a server shortcut and return the structured result."""

    if shortcut.kind != Shortcut.Kind.SERVER:
        raise ValidationError("Only server shortcuts can execute through listener endpoint.")
    shortcut.validate_target_fields(
        kind=shortcut.target_kind,
        identifier=shortcut.target_identifier,
        payload=shortcut.target_payload,
    )
    action_result = execute_shortcut_target(
        kind=shortcut.target_kind,
        identifier=shortcut.target_identifier,
        payload=shortcut.target_payload,
        context={"clipboard": "", "keyboard": "", "shortcut_key": shortcut.key_combo},
    )
    return ShortcutExecution(
        target_kind=shortcut.target_kind,
        target_identifier=shortcut.target_identifier,
        action_result=action_result,
        clipboard_output="",
        keyboard_output="",
        matched_pattern_id=None,
    )


def ensure_shortcut_listener_feature_enabled() -> bool:
    """Enable node feature assignment when suite feature is enabled and available."""

    if not is_suite_feature_enabled(SHORTCUT_MANAGEMENT_FEATURE_SLUG, default=False):
        return False

    feature = NodeFeature.objects.filter(slug=SHORTCUT_LISTENER_NODE_FEATURE_SLUG).first()
    node = Node.get_local()
    if feature is None or node is None:
        return False

    if not is_feature_active_for_node(node=node, slug=SHORTCUT_LISTENER_NODE_FEATURE_SLUG):
        return False

    NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

    Feature.objects.filter(slug=SHORTCUT_MANAGEMENT_FEATURE_SLUG, node_feature__isnull=True).update(
        node_feature=feature
    )
    return True


__all__ = [
    "ActionResult",
    "ShortcutExecution",
    "ShortcutExecutionError",
    "ensure_shortcut_listener_feature_enabled",
    "execute_client_shortcut",
    "execute_server_shortcut",
    "execute_shortcut_target",
    "render_shortcut_output",
]
