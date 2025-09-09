from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import shutil

from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _


def _gather_info() -> dict:
    """Collect basic system information similar to status-check.sh."""
    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / "locks"
    info: dict[str, object] = {}

    info["installed"] = (base_dir / ".venv").exists()

    service_file = lock_dir / "service.lck"
    info["service"] = (
        service_file.read_text().strip() if service_file.exists() else ""
    )

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

    info["features"] = {
        "celery": (lock_dir / "celery.lck").exists(),
        "lcd_screen": (lock_dir / "lcd_screen.lck").exists(),
        "control": (lock_dir / "control.lck").exists(),
    }

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
    if request.method == "POST" and request.user.is_superuser:
        action = request.POST.get("action")
        stop_script = Path(settings.BASE_DIR) / "stop.sh"
        args = [str(stop_script)]
        if action == "stop" and info["service"]:
            args.append("--all")
        subprocess.Popen(args)
        return redirect(reverse("admin:index"))

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
