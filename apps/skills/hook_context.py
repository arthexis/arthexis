from __future__ import annotations

import json
import platform as platform_module

from apps.features.models import Feature
from apps.nodes.feature_detection import is_feature_active_for_node
from apps.nodes.models import Node
from apps.skills.models import Hook


def current_hook_platform() -> str:
    system = platform_module.system().lower()
    if system.startswith("win"):
        return Hook.Platform.WINDOWS
    if system == "linux":
        return Hook.Platform.LINUX
    return Hook.Platform.ANY


def _suite_feature_enabled(feature: Feature, node: Node | None) -> bool:
    return feature.is_enabled and feature.is_enabled_for_node(node)


def _hook_matches_node(hook: Hook, node: Node | None) -> bool:
    node_roles = list(hook.node_roles.all())
    node_features = list(hook.node_features.all())
    suite_features = list(hook.suite_features.all())
    has_selectors = bool(node_roles or node_features or suite_features)
    if not has_selectors:
        return True
    role_id = getattr(node, "role_id", None)
    if role_id and any(role.pk == role_id for role in node_roles):
        return True
    if node is not None:
        for feature in node_features:
            if is_feature_active_for_node(node=node, slug=feature.slug):
                return True
    for feature in suite_features:
        if _suite_feature_enabled(feature, node):
            return True
    return False


def list_hooks(
    *,
    event: str | None = None,
    platform: str | None = None,
    node: Node | None = None,
) -> list[dict]:
    node = node if node is not None else Node.get_local()
    selected_platform = platform or current_hook_platform()
    queryset = Hook.objects.filter(enabled=True).prefetch_related(
        "node_roles", "node_features", "suite_features"
    )
    if event:
        queryset = queryset.filter(event=event)
    hooks = []
    for hook in queryset.order_by("event", "priority", "slug"):
        if hook.platform not in {Hook.Platform.ANY, selected_platform}:
            continue
        if not _hook_matches_node(hook, node):
            continue
        hooks.append(
            {
                "slug": hook.slug,
                "title": hook.title,
                "description": hook.description,
                "event": hook.event,
                "platform": hook.platform,
                "command": hook.command,
                "working_directory": hook.working_directory,
                "environment": hook.environment,
                "timeout_seconds": hook.timeout_seconds,
                "priority": hook.priority,
            }
        )
    return hooks


def render_hooks_json(
    *,
    event: str | None = None,
    platform: str | None = None,
    node: Node | None = None,
) -> str:
    return json.dumps(
        list_hooks(event=event, platform=platform, node=node),
        indent=2,
        sort_keys=True,
    )
