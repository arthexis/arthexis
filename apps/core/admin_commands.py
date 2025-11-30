"""Admin utilities for running Django management commands from the dashboard."""

from __future__ import annotations

import shlex
import time
import traceback
from datetime import timedelta
from io import StringIO

from django import forms
from django.contrib import admin, messages
from django.core import management
from django.core.exceptions import PermissionDenied
from django.core.management import BaseCommand, CommandError
from django.core.paginator import Paginator
from django.db import connections
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .models import AdminCommandResult
from .sigil_resolver import resolve_sigils


class RunCommandForm(forms.Form):
    command = forms.CharField(
        label=_("Command"),
        widget=forms.TextInput(attrs={"class": "vTextField", "placeholder": "check --deploy"}),
        help_text=_("Sigils will be resolved before running the command."),
    )

    def clean_command(self) -> str:
        command = self.cleaned_data["command"].strip()
        if not command:
            raise forms.ValidationError(_("Enter a command to run."))
        try:
            shlex.split(command)
        except ValueError as exc:
            raise forms.ValidationError(_("Invalid command: %(error)s"), params={"error": exc})
        return command


def _load_command(command_name: str) -> BaseCommand:
    commands = management.get_commands()
    if command_name not in commands:
        raise CommandError(_("Unknown command: %(command)s") % {"command": command_name})

    app_name = commands[command_name]
    if isinstance(app_name, BaseCommand):
        return app_name
    return management.load_command_class(app_name, command_name)


def run_management_command(raw_command: str, user) -> AdminCommandResult:
    resolved_command = resolve_sigils(raw_command)
    stdout = StringIO()
    stderr = StringIO()
    traceback_text = ""
    success = False
    exit_code = 0
    command_name = ""

    start = time.monotonic()
    try:
        parts = shlex.split(resolved_command)
    except ValueError as exc:
        stderr.write(str(exc))
        traceback_text = traceback.format_exc()
        exit_code = 1
    else:
        if not parts:
            stderr.write(str(_("No command provided.")))
            exit_code = 1
        else:
            command_name, *args = parts
            try:
                command = _load_command(command_name)
                command.stdout = stdout
                command.stderr = stderr
                try:
                    command.run_from_argv(["manage.py", command_name, *args])
                except SystemExit as exc:
                    exit_code = exc.code or 0
                    success = exit_code == 0
                    if not success:
                        traceback_text = traceback.format_exc()
                else:
                    success = True
            except Exception as exc:  # pragma: no cover - defensive runtime capture
                if not stderr.getvalue():
                    stderr.write(str(exc))
                traceback_text = traceback.format_exc()
                exit_code = getattr(exc, "code", 1) or 1
    runtime = timedelta(seconds=time.monotonic() - start)

    connection = connections["default"]
    connection.ensure_connection()

    result = AdminCommandResult.objects.using(connection.alias).create(
        command=raw_command,
        resolved_command=resolved_command,
        command_name=command_name,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        traceback=traceback_text,
        runtime=runtime,
        exit_code=exit_code,
        success=success,
        user=user if getattr(user, "is_authenticated", False) else None,
    )
    return result


def run_command_view(request: HttpRequest) -> HttpResponse:
    if not request.user.is_superuser:
        raise PermissionDenied

    form = RunCommandForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = run_management_command(form.cleaned_data["command"], request.user)
        message = _("Command ran successfully.") if result.success else _(
            "Command failed. See details below."
        )
        message_level = messages.SUCCESS if result.success else messages.ERROR
        messages.add_message(request, message_level, message)
        return redirect(f"{reverse('admin:run_command')}?result={result.pk}")

    result = None
    selected = request.GET.get("result")
    if selected:
        result = AdminCommandResult.objects.filter(pk=selected).first()

    history = AdminCommandResult.objects.select_related("user")
    paginator = Paginator(history, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    if result is None:
        result = page_obj.object_list[0] if page_obj.object_list else None

    context = {
        **admin.site.each_context(request),
        "title": _("Run Command"),
        "form": form,
        "result": result,
        "page_obj": page_obj,
        "paginator": paginator,
    }
    return TemplateResponse(request, "admin/run_command.html", context)


def patch_admin_command_runner() -> None:
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("run-command/", admin.site.admin_view(run_command_view), name="run_command"),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
