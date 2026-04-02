from __future__ import annotations

from pathlib import Path

from .feature_detection import NodeFeatureDetectionRegistry


FEATURE_LOCK_MAP = {
    "celery-queue": "celery.lck",
    "nginx-server": "nginx_mode.lck",
}
SYSTEMD_DEPENDENT_FEATURE_SLUGS = frozenset(FEATURE_LOCK_MAP)


def _lock_detected(*, slug: str, base_dir: Path, base_path: Path) -> bool:
    """Return whether lockfiles indicate a managed feature is active."""

    lock = FEATURE_LOCK_MAP.get(slug)
    if not lock:
        return False

    lock_dirs = {base_path / ".locks", base_dir / ".locks"}
    return any(lock_dir.joinpath(lock).exists() for lock_dir in lock_dirs)


def check_node_feature(
    slug: str,
    *,
    node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Evaluate node-owned auto-managed feature slugs."""

    del node
    if slug in SYSTEMD_DEPENDENT_FEATURE_SLUGS:
        from apps.nodes.models.features import _systemctl_command

        if not bool(_systemctl_command()):
            return False
    if slug in FEATURE_LOCK_MAP:
        return _lock_detected(slug=slug, base_dir=base_dir, base_path=base_path)
    if slug == "gui-toast":
        from apps.core.notifications import supports_gui_toast

        return supports_gui_toast()
    if slug == "video-cam":
        from apps.video import has_rpi_camera_stack

        return has_rpi_camera_stack()
    if slug == "gpio-rtc":
        from apps.nodes.models.features import has_clock_device

        return has_clock_device()
    return None


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register node app feature auto-detection callbacks."""

    registry.register("*", check=check_node_feature)


__all__ = ["check_node_feature", "register_node_feature_detection"]
