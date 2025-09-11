from __future__ import annotations

from pathlib import Path
import io
import socket
import subprocess
import shutil
import argparse

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.management import get_commands, load_command_class
from django.http import Http404
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

    excluded = {
        "shell",
        "dbshell",
        "createsuperuser",
        "changepassword",
        "startapp",
        "startproject",
        "runserver",
    }
    commands = sorted(cmd for cmd in get_commands().keys() if cmd not in excluded)

    context = admin.site.each_context(request)
    context.update({"title": _("System"), "info": info, "commands": commands})
    return TemplateResponse(request, "admin/system.html", context)


def _build_form(parser: argparse.ArgumentParser) -> type[forms.Form]:
    fields: dict[str, forms.Field] = {}
    for action in parser._actions:
        if action.help == argparse.SUPPRESS or action.dest == "help":
            continue
        label = action.option_strings[0] if action.option_strings else action.dest
        required = (
            action.required
            if action.option_strings
            else action.nargs not in ["?", "*", argparse.OPTIONAL]
        )
        fields[action.dest] = forms.CharField(label=label, required=required)
    return type("CommandForm", (forms.Form,), fields)


def _system_command_view(request, command):
    commands = get_commands()
    if command not in commands:
        raise Http404
    app_name = commands[command]
    cmd_instance = load_command_class(app_name, command)
    parser = cmd_instance.create_parser("manage.py", command)
    form_class = _build_form(parser)
    form = form_class(request.POST or None)
    output = ""

    has_required = any(
        (a.option_strings and a.required)
        or (not a.option_strings and a.nargs not in ["?", "*", argparse.OPTIONAL])
        for a in parser._actions
        if a.help != argparse.SUPPRESS and a.dest != "help"
    )

    if not has_required and request.method == "GET":
        out = io.StringIO()
        cmd_instance.stdout = out
        cmd_instance.stderr = out
        try:
            cmd_instance.run_from_argv(["manage.py", command])
        except Exception as exc:
            out.write(str(exc))
        output = out.getvalue()
        form = None
    elif request.method == "POST" and form.is_valid():
        argv = ["manage.py", command]
        for action in parser._actions:
            if action.help == argparse.SUPPRESS or action.dest == "help":
                continue
            val = form.cleaned_data.get(action.dest)
            if val in (None, ""):
                continue
            if action.option_strings:
                argv.append(action.option_strings[0])
                if action.nargs != 0:
                    argv.append(val)
            else:
                argv.append(val)
        out = io.StringIO()
        cmd_instance.stdout = out
        cmd_instance.stderr = out
        try:
            cmd_instance.run_from_argv(argv)
        except Exception as exc:
            out.write(str(exc))
        output = out.getvalue()
        form = None

    context = admin.site.each_context(request)
    context.update(
        {
            "title": command,
            "command_name": command,
            "form": form,
            "output": output,
        }
    )
    return TemplateResponse(request, "admin/system_command.html", context)


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
            path(
                "system/command/<str:command>/",
                admin.site.admin_view(_system_command_view),
                name="system_command",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
