from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _


def _get_django_settings():
    return sorted(
        [(name, getattr(settings, name)) for name in dir(settings) if name.isupper()]
    )


class NetworkSetupForm(forms.Form):
    prompt_for_password = forms.BooleanField(
        label=_("Prompt for new WiFi password"),
        required=False,
        help_text=_("Add --password to request a password even when one is already configured."),
    )
    access_point_name = forms.CharField(
        label=_("Access point name"),
        required=False,
        max_length=32,
        help_text=_("Use --ap to set the wlan0 access point name."),
    )
    skip_firewall_validation = forms.BooleanField(
        label=_("Skip firewall validation"),
        required=False,
        help_text=_("Add --no-firewall to bypass firewall port checks."),
    )
    skip_access_point_configuration = forms.BooleanField(
        label=_("Skip access point configuration"),
        required=False,
        help_text=_("Add --no-ap to leave the access point configuration unchanged."),
    )
    allow_unsafe_changes = forms.BooleanField(
        label=_("Allow modifying the active internet connection"),
        required=False,
        help_text=_("Include --unsafe to allow changes that may interrupt connectivity."),
    )
    interactive = forms.BooleanField(
        label=_("Prompt before each step"),
        required=False,
        help_text=_("Run the script with --interactive to confirm each action."),
    )
    install_watchdog = forms.BooleanField(
        label=_("Install WiFi watchdog service"),
        required=False,
        initial=True,
        help_text=_("Keep selected to retain the watchdog or clear to add --no-watchdog."),
    )
    vnc_validation = forms.ChoiceField(
        label=_("VNC validation"),
        choices=(
            ("default", _("Use script default (skip validation)")),
            ("require", _("Require that a VNC service is enabled (--vnc)")),
        ),
        initial="default",
        required=True,
    )
    ethernet_subnet = forms.CharField(
        label=_("Ethernet subnet"),
        required=False,
        help_text=_("Provide N or N/P (prefix 16 or 24) to supply --subnet."),
    )
    update_ap_password_only = forms.BooleanField(
        label=_("Update access point password only"),
        required=False,
        help_text=_("Use --ap-set-password without running other setup steps."),
    )

    def clean_ethernet_subnet(self) -> str:
        value = self.cleaned_data.get("ethernet_subnet", "")
        if not value:
            return ""
        raw = value.strip()
        match = re.fullmatch(r"(?P<subnet>\d{1,3})(?:/(?P<prefix>\d{1,2}))?", raw)
        if not match:
            raise forms.ValidationError(
                _("Enter a subnet in the form N or N/P with prefix 16 or 24."),
            )
        subnet = int(match.group("subnet"))
        if subnet < 0 or subnet > 254:
            raise forms.ValidationError(
                _("Subnet value must be between 0 and 254."),
            )
        prefix_value = match.group("prefix")
        if prefix_value:
            prefix = int(prefix_value)
            if prefix not in {16, 24}:
                raise forms.ValidationError(
                    _("Subnet prefix must be 16 or 24."),
                )
            return f"{subnet}/{prefix}"
        return str(subnet)

    def clean(self) -> dict:
        cleaned_data = super().clean()
        if cleaned_data.get("update_ap_password_only"):
            other_flags = [
                cleaned_data.get("prompt_for_password"),
                bool(cleaned_data.get("access_point_name")),
                cleaned_data.get("skip_firewall_validation"),
                cleaned_data.get("skip_access_point_configuration"),
                cleaned_data.get("allow_unsafe_changes"),
                cleaned_data.get("interactive"),
                bool(cleaned_data.get("ethernet_subnet")),
                cleaned_data.get("vnc_validation") == "require",
                not cleaned_data.get("install_watchdog", True),
            ]
            if any(other_flags):
                raise forms.ValidationError(
                    _(
                        "Update access point password only cannot be combined with other network-setup options."
                    )
                )
        return cleaned_data

    def build_command(self, script_path: Path) -> list[str]:
        command = [str(script_path)]
        data = self.cleaned_data
        if data.get("update_ap_password_only"):
            command.append("--ap-set-password")
            return command
        if data.get("prompt_for_password"):
            command.append("--password")
        access_point_name = data.get("access_point_name")
        if access_point_name:
            command.extend(["--ap", access_point_name])
        if data.get("skip_firewall_validation"):
            command.append("--no-firewall")
        if data.get("skip_access_point_configuration"):
            command.append("--no-ap")
        if data.get("allow_unsafe_changes"):
            command.append("--unsafe")
        if data.get("interactive"):
            command.append("--interactive")
        if not data.get("install_watchdog"):
            command.append("--no-watchdog")
        if data.get("vnc_validation") == "require":
            command.append("--vnc")
        ethernet_subnet = data.get("ethernet_subnet")
        if ethernet_subnet:
            command.extend(["--subnet", ethernet_subnet])
        return command


def _environment_view(request):
    env_vars = sorted(os.environ.items())
    context = admin.site.each_context(request)
    environment_tasks: list[dict[str, str]] = []
    if request.user.is_superuser:
        environment_tasks.append(
            {
                "name": _("Run network-setup"),
                "description": _(
                    "Configure network services, stage managed NGINX sites, and review script output."
                ),
                "url": reverse("admin:environment-network-setup"),
            }
        )
    context.update(
        {
            "title": _("Environment"),
            "env_vars": env_vars,
            "environment_tasks": environment_tasks,
        }
    )
    return TemplateResponse(request, "admin/environment.html", context)


def _environment_network_setup_view(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    script_path = Path(settings.BASE_DIR) / "network-setup.sh"
    command_result: dict[str, object] | None = None

    if request.method == "POST":
        form = NetworkSetupForm(request.POST)
        if form.is_valid():
            command = form.build_command(script_path)
            if not script_path.exists():
                form.add_error(None, _("The network-setup.sh script could not be found."))
            else:
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        cwd=settings.BASE_DIR,
                        check=False,
                    )
                except FileNotFoundError:
                    form.add_error(None, _("The network-setup.sh script could not be executed."))
                except OSError as exc:
                    form.add_error(
                        None,
                        _("Unable to execute network-setup.sh: %(error)s")
                        % {"error": str(exc)},
                    )
                else:
                    if hasattr(shlex, "join"):
                        command_display = shlex.join(command)
                    else:
                        command_display = " ".join(shlex.quote(part) for part in command)
                    command_result = {
                        "command": command_display,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "returncode": completed.returncode,
                        "succeeded": completed.returncode == 0,
                    }
    else:
        form = NetworkSetupForm()

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Run network-setup"),
            "form": form,
            "command_result": command_result,
            "task_description": _(
                "Configure script flags, execute network-setup, and review the captured output."
            ),
            "back_url": reverse("admin:environment"),
        }
    )
    return TemplateResponse(request, "admin/environment_network_setup.html", context)


def _config_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Django Settings"),
            "django_settings": _get_django_settings(),
        }
    )
    return TemplateResponse(request, "admin/config.html", context)


def patch_admin_environment_view() -> None:
    """Register the Environment and Config admin views on the main admin site."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "environment/network-setup/",
                admin.site.admin_view(_environment_network_setup_view),
                name="environment-network-setup",
            ),
            path(
                "environment/",
                admin.site.admin_view(_environment_view),
                name="environment",
            ),
            path(
                "config/",
                admin.site.admin_view(_config_view),
                name="config",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
