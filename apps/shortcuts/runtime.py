"""Runtime helpers for executing shortcuts and auto-enabling listener features."""

from __future__ import annotations

from dataclasses import dataclass
import re

from django.core.exceptions import ValidationError

from apps.features.models import Feature
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.feature_detection import is_feature_active_for_node
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.sigils.sigil_resolver import resolve_sigils

from .constants import SHORTCUT_LISTENER_NODE_FEATURE_SLUG, SHORTCUT_MANAGEMENT_FEATURE_SLUG
from .models import ClipboardPattern, Shortcut

_ARG_TOKEN = re.compile(r"\[ARG\.([^\]]+)\]")


@dataclass(frozen=True)
class ShortcutExecution:
    """Structured shortcut execution output."""

    selection: str
    rendered_output: str
    clipboard_output: str
    keyboard_output: str
    matched_pattern_id: int | None


def _resolve_arg_tokens(template: str, context: dict[str, str]) -> str:
    """Expand ``[ARG.*]`` placeholders from the provided context mapping."""

    if not template:
        return ""

    def replace(match: re.Match[str]) -> str:
        return str(context.get(match.group(1), ""))

    return _ARG_TOKEN.sub(replace, template)


def render_shortcut_output(template: str, *, context: dict[str, str]) -> str:
    """Render output text by combining sigils and argument placeholders."""

    if not template:
        return ""
    rendered = resolve_sigils(template, current=None)
    return _resolve_arg_tokens(rendered, context)


def _select_pattern(shortcut: Shortcut, clipboard: str) -> ClipboardPattern | None:
    """Return the first active pattern that matches ``clipboard`` text."""

    if not shortcut.use_clipboard_patterns:
        return None

    for pattern in shortcut.clipboard_patterns.filter(is_active=True).order_by("priority", "pk"):
        if re.search(pattern.pattern, clipboard or ""):
            return pattern
    return None


def execute_client_shortcut(*, shortcut: Shortcut, clipboard: str, keyboard: str = "") -> ShortcutExecution:
    """Execute a client shortcut and return typed/clipboard output payload."""

    if shortcut.kind != Shortcut.Kind.CLIENT:
        raise ValidationError("Only client shortcuts can execute through browser endpoint.")

    selected_pattern = _select_pattern(shortcut, clipboard)
    target = selected_pattern or shortcut
    context = {
        "clipboard": clipboard,
        "keyboard": keyboard,
        "shortcut_key": shortcut.key_combo,
    }
    value = render_shortcut_output((target.output_template or "").strip(), context=context)

    clipboard_enabled = bool(getattr(target, "clipboard_output_enabled", False))
    keyboard_enabled = bool(getattr(target, "keyboard_output_enabled", False))
    return ShortcutExecution(
        selection=target.display,
        rendered_output=value,
        clipboard_output=value if clipboard_enabled else "",
        keyboard_output=value if keyboard_enabled else "",
        matched_pattern_id=getattr(selected_pattern, "pk", None),
    )


def execute_server_shortcut(*, shortcut: Shortcut) -> str:
    """Render the configured output for a server shortcut."""

    if shortcut.kind != Shortcut.Kind.SERVER:
        raise ValidationError("Only server shortcuts can execute through the listener.")

    value = render_shortcut_output(
        (shortcut.output_template or "").strip(),
        context={"shortcut_key": shortcut.key_combo},
    )
    return value


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
    "execute_server_shortcut",
    "render_shortcut_output",
]
