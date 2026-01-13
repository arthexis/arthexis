from __future__ import annotations

import contextlib
import logging
import os

import requests
from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin import (
    EntityModelAdmin,
    OwnableAdminMixin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
    _build_credentials_actions,
)
from apps.release.models import ReleaseManager

logger = logging.getLogger(__name__)


class ReleaseManagerAdminForm(forms.ModelForm):
    class Meta:
        model = ReleaseManager
        fields = "__all__"
        widgets = {
            "pypi_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "github_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "git_password": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pypi_token"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Generate an API token from your PyPI account settings.",
            "https://pypi.org/manage/account/token/",
            "pypi.org/manage/account/token/",
            (
                " by clicking “Add API token”, optionally scoping it to the package, "
                "and paste the full `pypi-***` value here."
            ),
        )
        self.fields["github_token"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Create a personal access token at GitHub → Settings → Developer settings →",
            "https://github.com/settings/tokens",
            "github.com/settings/tokens",
            (
                " with the repository access needed for releases (repo scope for classic tokens "
                "or an equivalent fine-grained token) and paste it here."
            ),
        )
        self.fields["git_username"].help_text = (
            "Username used for HTTPS git pushes (for example, your GitHub username)."
        )
        self.fields["git_password"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Provide the password or personal access token used for pushing tags. ",
            "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token",
            "docs.github.com/.../creating-a-personal-access-token",
            " If left blank, the GitHub token will be used instead.",
        )


class ReleaseManagerAdmin(
    OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    form = ReleaseManagerAdminForm
    list_display = (
        "owner",
        "has_github_credentials",
        "has_pypi_credentials",
        "pypi_username",
        "pypi_url",
        "secondary_pypi_url",
    )
    actions = ["test_credentials"]
    change_actions = ["test_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (
            "PyPI",
            {
                "fields": (
                    "pypi_username",
                    "pypi_token",
                    "pypi_password",
                    "pypi_url",
                    "secondary_pypi_url",
                )
            },
        ),
        (
            "GitHub",
            {
                "fields": (
                    "github_token",
                    "git_username",
                    "git_password",
                )
            },
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    def has_github_credentials(self, obj):
        return obj.to_git_credentials() is not None

    has_github_credentials.boolean = True
    has_github_credentials.short_description = "GitHub"

    def has_pypi_credentials(self, obj):
        return obj.to_credentials() is not None

    has_pypi_credentials.boolean = True
    has_pypi_credentials.short_description = "PyPI"

    def _test_credentials(self, request, manager):
        pypi_creds = manager.to_credentials()
        git_creds = manager.to_git_credentials()
        has_github_fields = bool(
            manager.github_token or manager.git_username or manager.git_password
        )
        errors = []
        if not pypi_creds:
            errors.append("PyPI credentials missing (token or username/password)")
        if has_github_fields and not git_creds:
            errors.append("GitHub credentials incomplete (missing username or token)")
        if errors:
            for error in errors:
                self.message_user(request, f"{manager}: {error}", messages.ERROR)
        if pypi_creds:
            self._test_pypi_credentials(request, manager, pypi_creds)
        if git_creds:
            self._test_github_credentials(request, manager, git_creds)

    def _test_pypi_credentials(self, request, manager, creds):
        env_url = os.environ.get("PYPI_REPOSITORY_URL", "").strip()
        url = env_url or "https://upload.pypi.org/legacy/"
        uses_token = bool(creds.token)
        auth = (
            ("__token__", creds.token)
            if uses_token
            else (creds.username, creds.password)
        )
        auth_label = "token" if uses_token else "username/password"
        resp = None
        try:
            resp = requests.post(
                url,
                auth=auth,
                data={"verify_credentials": "1"},
                timeout=10,
                allow_redirects=False,
            )
            status = resp.status_code
            if status in {401, 403}:
                self.message_user(
                    request,
                    f"{manager} PyPI {auth_label} invalid ({status})",
                    messages.ERROR,
                )
            elif status <= 400:
                suffix = f" ({status})" if status != 200 else ""
                self.message_user(
                    request,
                    f"{manager} PyPI {auth_label} valid{suffix}",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    (
                        f"{manager} PyPI {auth_label} check returned status "
                        f"{status} for {url}"
                    ),
                    messages.ERROR,
                )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request,
                f"{manager} PyPI {auth_label} check failed: {exc}",
                messages.ERROR,
            )
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    def _test_github_credentials(self, request, manager, git_creds):
        url = "https://api.github.com/user"
        auth_label = (
            "token" if git_creds.username == "x-access-token" else "username/password"
        )
        headers = {}
        auth = (git_creds.username, git_creds.password)
        resp = None
        try:
            resp = requests.get(
                url,
                headers=headers,
                auth=auth,
                timeout=10,
                allow_redirects=False,
            )
            status = resp.status_code
            if status in {401, 403}:
                self.message_user(
                    request,
                    f"{manager} GitHub {auth_label} invalid ({status})",
                    messages.ERROR,
                )
            elif 200 <= status < 300:
                self.message_user(
                    request,
                    f"{manager} GitHub {auth_label} valid ({status})",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    (
                        f"{manager} GitHub {auth_label} check returned status "
                        f"{status} for {url}"
                    ),
                    messages.ERROR,
                )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request,
                f"{manager} GitHub {auth_label} check failed: {exc}",
                messages.ERROR,
            )
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    (
        test_credentials,
        test_credentials_action,
    ) = _build_credentials_actions("test_credentials", "_test_credentials")


__all__ = [
    "ReleaseManagerAdmin",
    "ReleaseManagerAdminForm",
]
