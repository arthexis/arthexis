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
from apps.recipes.utils import resolve_arg_sigils
from apps.sigils.sigil_resolver import resolve_sigils

from .constants import SHORTCUT_LISTENER_NODE_FEATURE_SLUG, SHORTCUT_MANAGEMENT_FEATURE_SLUG
from .models import ClipboardPattern, Shortcut


@dataclass(frozen=True)
class ShortcutExecution:
    """Structured shortcut execution output."""

    recipe_slug: str
    recipe_result: Any
    clipboard_output: str
    keyboard_output: str
    matched_pattern_id: int | None


def render_shortcut_output(template: str, *, context: dict[str, Any]) -> str:
    """Render output text by combining sigils and argument placeholders."""

    if not template:
        return ""
    rendered = resolve_sigils(template, current=None)
    return resolve_arg_sigils(rendered, (), context)


def _select_pattern(shortcut: Shortcut, clipboard: str) -> ClipboardPattern | None:
    """Return the first active pattern that matches ``clipboard`` text."""

    if not shortcut.use_clipboard_patterns:
        return None

    for pattern in shortcut.clipboard_patterns.filter(is_active=True).order_by("priority", "pk"):
        if re.search(pattern.pattern, clipboard or ""):
            return pattern
    return None


def _sanitize_recipe_argument(value: str) -> str:
    """Escape user-provided text to reduce Python argument-substitution risks."""

    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace('"', '\\"')
        .replace("'", "\\'")
    )


def execute_client_shortcut(*, shortcut: Shortcut, clipboard: str, keyboard: str = "") -> ShortcutExecution:
    """Execute a client shortcut and return typed/clipboard output payload."""

    if shortcut.kind != Shortcut.Kind.CLIENT:
        raise ValidationError("Only client shortcuts can execute through browser endpoint.")

    selected_pattern = _select_pattern(shortcut, clipboard)
    target = selected_pattern or shortcut
    recipe = getattr(target, "recipe", None) or shortcut.recipe
    if recipe is None:
        raise ValidationError("No recipe configured for this shortcut execution.")

    sanitized_clipboard = _sanitize_recipe_argument(clipboard)
    sanitized_keyboard = _sanitize_recipe_argument(keyboard)
    execution = recipe.execute(
        clipboard=sanitized_clipboard,
        keyboard=sanitized_keyboard,
        shortcut_key=shortcut.key_combo,
    )
    context = {
        "clipboard": clipboard,
        "keyboard": keyboard,
        "recipe_result": execution.result,
        "shortcut_key": shortcut.key_combo,
    }
    output_template = (getattr(target, "output_template", "") or "").strip()
    rendered_output = render_shortcut_output(output_template, context=context)
    fallback_output = str(execution.result) if execution.result is not None else ""
    value = rendered_output or fallback_output

    clipboard_enabled = bool(getattr(target, "clipboard_output_enabled", False))
    keyboard_enabled = bool(getattr(target, "keyboard_output_enabled", False))
    return ShortcutExecution(
        recipe_slug=recipe.slug,
        recipe_result=execution.result,
        clipboard_output=value if clipboard_enabled else "",
        keyboard_output=value if keyboard_enabled else "",
        matched_pattern_id=getattr(selected_pattern, "pk", None),
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
    "ShortcutExecution",
    "ensure_shortcut_listener_feature_enabled",
    "execute_client_shortcut",
    "render_shortcut_output",
]
