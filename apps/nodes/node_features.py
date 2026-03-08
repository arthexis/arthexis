from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from .feature_detection import NodeFeatureDetectionRegistry


FEATURE_LOCK_MAP = {
    "celery-queue": "celery.lck",
    "nginx-server": "nginx_mode.lck",
}
SYSTEMD_DEPENDENT_FEATURE_SLUGS = frozenset(FEATURE_LOCK_MAP)
AP_ROUTER_SSID = "gelectriic-ap"
NMCLI_TIMEOUT = 5


def _lock_detected(*, slug: str, base_dir: Path, base_path: Path) -> bool:
    """Return whether lockfiles indicate a managed feature is active."""

    lock = FEATURE_LOCK_MAP.get(slug)
    if not lock:
        return False

    lock_dirs = {base_path / ".locks", base_dir / ".locks"}
    return any(lock_dir.joinpath(lock).exists() for lock_dir in lock_dirs)


def _hosts_gelectriic_ap() -> bool:
    """Return ``True`` when the node is hosting the gelectriic access point."""

    nmcli_path = shutil.which("nmcli")
    if not nmcli_path:
        return False
    try:
        result = subprocess.run(
            [
                nmcli_path,
                "-t",
                "-f",
                "NAME,DEVICE,TYPE",
                "connection",
                "show",
                "--active",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=NMCLI_TIMEOUT,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split(":", 2)
        if not parts:
            continue
        name = parts[0]
        conn_type = ""
        if len(parts) == 3:
            conn_type = parts[2]
        elif len(parts) > 1:
            conn_type = parts[1]
        if name != AP_ROUTER_SSID:
            continue
        conn_type_normalized = conn_type.strip().lower()
        if conn_type_normalized not in {"wifi", "802-11-wireless"}:
            continue
        try:
            mode_result = subprocess.run(
                [
                    nmcli_path,
                    "-g",
                    "802-11-wireless.mode",
                    "connection",
                    "show",
                    name,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=NMCLI_TIMEOUT,
            )
        except Exception:
            continue
        if mode_result.returncode != 0:
            continue
        if mode_result.stdout.strip() == "ap":
            return True
    return False


def check_node_feature(
    slug: str,
    *,
    node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Evaluate node-owned auto-managed feature slugs."""

    del node
    if slug == "systemd-manager":
        from apps.core.systemctl import _systemctl_command

        return bool(_systemctl_command())
    if slug in SYSTEMD_DEPENDENT_FEATURE_SLUGS:
        from apps.core.systemctl import _systemctl_command

        if not bool(_systemctl_command()):
            return False
    if slug in FEATURE_LOCK_MAP:
        return _lock_detected(slug=slug, base_dir=base_dir, base_path=base_path)
    if slug == "gui-toast":
        from apps.core.notifications import supports_gui_toast

        return supports_gui_toast()
    if slug == "video-cam":
        from apps.content.video import has_rpi_camera_stack

        return has_rpi_camera_stack()
    if slug == "ap-router":
        return _hosts_gelectriic_ap()
    if slug == "gpio-rtc":
        from apps.clocks.utils import has_clock_device

        return has_clock_device()
    return None


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register node app feature auto-detection callbacks."""

    registry.register("*", check=check_node_feature)


__all__ = ["check_node_feature", "register_node_feature_detection"]
