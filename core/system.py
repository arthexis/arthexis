from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import shutil

from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _


def _gather_info() -> dict:
    """Collect basic system information similar to status.sh."""
    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / "locks"
    info: dict[str, object] = {}

    info["installed"] = (base_dir / ".venv").exists()

    service_file = lock_dir / "service.lck"
    info["service"] = service_file.read_text().strip() if service_file.exists() else ""

    mode_file = lock_dir / "nginx_mode.lck"
    mode = mode_file.read_text().strip() if mode_file.exists() else "internal"
    info["mode"] = mode
    info["port"] = 8000 if mode == "public" else 8888

    screen_file = lock_dir / "screen_mode.lck"
    info["screen_mode"] = (
        screen_file.read_text().strip() if screen_file.exists() else ""
    )

    # Use settings.NODE_ROLE as the single source of truth for the node role.
    info["role"] = getattr(settings, "NODE_ROLE", "Terminal")

    features: list[dict[str, object]] = []
    try:
        from nodes.models import Node, NodeFeature
    except Exception:
        info["features"] = features
    else:
        feature_map: dict[str, dict[str, object]] = {}

        def _add_feature(feature: NodeFeature, flag: str) -> None:
            slug = getattr(feature, "slug", "") or ""
            if not slug:
                return
            display = (getattr(feature, "display", "") or "").strip()
            normalized = display or slug.replace("-", " ").title()
            entry = feature_map.setdefault(
                slug,
                {
                    "slug": slug,
                    "display": normalized,
                    "expected": False,
                    "actual": False,
                },
            )
            if display:
                entry["display"] = display
            entry[flag] = True

        try:
            expected_features = (
                NodeFeature.objects.filter(roles__name=info["role"]).only("slug", "display").distinct()
            )
        except Exception:
            expected_features = []
        try:
            for feature in expected_features:
                _add_feature(feature, "expected")
        except Exception:
            pass

        try:
            local_node = Node.get_local()
        except Exception:
            local_node = None

        actual_features = []
        if local_node:
            try:
                actual_features = list(local_node.features.only("slug", "display"))
            except Exception:
                actual_features = []

        try:
            for feature in actual_features:
                _add_feature(feature, "actual")
        except Exception:
            pass

        features = sorted(
            feature_map.values(),
            key=lambda item: str(item.get("display", "")).lower(),
        )
        info["features"] = features

    running = False
    service_status = ""
    service = info["service"]
    if service and shutil.which("systemctl"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", str(service)],
                capture_output=True,
                text=True,
                check=False,
            )
            service_status = result.stdout.strip()
            running = service_status == "active"
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["pgrep", "-f", "manage.py runserver"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            running = True
        except Exception:
            running = False
    info["running"] = running
    info["service_status"] = service_status

    try:
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
    except Exception:
        hostname = ""
        ip_list = []
    info["hostname"] = hostname
    info["ip_addresses"] = ip_list

    return info


def _system_view(request):
    info = _gather_info()

    context = admin.site.each_context(request)
    context.update({"title": _("System"), "info": info})
    return TemplateResponse(request, "admin/system.html", context)


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
