from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from apps.core.systemctl import _systemctl_command

from .services import is_desktop_ui_available, resolve_browser_opener

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


def get_desktop_launch_prereq_state(
    *, base_dir: Path, base_path: Path
) -> dict[str, bool]:
    """Return desktop-launch prerequisite state for the local desktop environment."""

    del base_dir
    del base_path
    return {
        "desktop_context_ready": is_desktop_ui_available(),
        "systemd_control_available": bool(_systemctl_command()),
        "browser_opener_available": bool(resolve_browser_opener()),
    }


def check_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Resolve desktop-related node feature checks implemented by this app."""

    base_dir = Path(settings.BASE_DIR)
    base_path = node.get_base_path()
    prereqs = get_desktop_launch_prereq_state(base_dir=base_dir, base_path=base_path)

    if slug == "user-desktop":
        return prereqs["desktop_context_ready"]
    if slug == "systemd-manager":
        return prereqs["systemd_control_available"]
    return None


def setup_node_feature(slug: str, *, node: "Node") -> bool | None:
    """Allow desktop features to own setup lifecycle while sharing check logic."""

    return check_node_feature(slug, node=node)


__all__ = [
    "check_node_feature",
    "get_desktop_launch_prereq_state",
    "setup_node_feature",
]
