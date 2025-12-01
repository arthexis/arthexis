from __future__ import annotations

import contextlib
import datetime
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from packaging.version import Version

from apps.core.admin import (
    EntityModelAdmin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
    _build_credentials_actions,
)
from apps.locals.user_data import delete_user_fixture, dump_user_fixture
from apps.release import release as release_utils
from apps.release.models import Package, PackageRelease, ReleaseManager
from apps.repos.forms import PackageRepositoryForm
from apps.repos.task_utils import GitHubRepositoryError, create_repository_for_package

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


@admin.register(ReleaseManager)
class ReleaseManagerAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
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
        creds = manager.to_credentials()
        if not creds:
            self.message_user(request, f"{manager} has no credentials", messages.ERROR)
            return
        env_url = os.environ.get("PYPI_REPOSITORY_URL", "").strip()
        url = env_url or "https://upload.pypi.org/legacy/"
        auth = (
            ("__token__", creds.token)
            if creds.token
            else (creds.username, creds.password)
        )
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
                    f"{manager} credentials invalid ({status})",
                    messages.ERROR,
                )
            elif status <= 400:
                suffix = f" ({status})" if status != 200 else ""
                self.message_user(
                    request,
                    f"{manager} credentials valid{suffix}",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"{manager} credentials check returned unexpected status {status}",
                    messages.ERROR,
                )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{manager} credentials check failed: {exc}", messages.ERROR
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


@admin.register(Package)
class PackageAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    actions = ["create_repository_bulk_action"]
    list_display = (
        "name",
        "description",
        "homepage_url",
        "release_manager",
        "is_active",
    )
    change_actions = ["create_repository_action", "prepare_next_release_action"]

    def _prepare(self, request, package):
        if request.method not in {"POST", "GET"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        from pathlib import Path
        from packaging.version import Version

        ver_file = Path("VERSION")
        if ver_file.exists():
            raw_version = ver_file.read_text().strip()
            repo_version_text = PackageRelease.normalize_version(raw_version) or "0.0.0"
            repo_version = Version(repo_version_text)
        else:
            repo_version = Version("0.0.0")

        pypi_latest = Version("0.0.0")
        resp = None
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{package.name}/json", timeout=10
            )
            if resp.ok:
                releases = resp.json().get("releases", {})
                if releases:
                    pypi_latest = max(Version(v) for v in releases)
        except Exception:
            pass
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
        pypi_plus_one = Version(
            PackageRelease._format_patch_with_epoch(pypi_latest)
        )
        next_version = max(repo_version, pypi_plus_one)
        release, _created = PackageRelease.all_objects.update_or_create(
            package=package,
            version=str(next_version),
            defaults={
                "release_manager": package.release_manager,
                "is_deleted": False,
            },
        )
        return redirect(reverse("admin:release_packagerelease_change", args=[release.pk]))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:object_id>/create-repository/",
                self.admin_site.admin_view(self.create_repository_view),
                name="release_package_create_repository",
            ),
            path(
                "prepare-next-release/",
                self.admin_site.admin_view(self.prepare_next_release_active),
                name="release_package_prepare_next_release",
            )
        ]
        return custom + urls

    def create_repository_action(self, request, obj):
        url = reverse("admin:release_package_create_repository", args=[obj.pk])
        return redirect(url)

    create_repository_action.label = _("Create GitHub repository")
    create_repository_action.short_description = _("Create GitHub repository")

    def prepare_next_release_active(self, request):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return redirect("admin:release_package_changelist")
        return self._prepare(request, package)

    def prepare_next_release_action(self, request, obj):
        return self._prepare(request, obj)

    prepare_next_release_action.label = "Prepare next Release"
    prepare_next_release_action.short_description = "Prepare next release"

    @admin.action(description=_("Create GitHub repository"))
    def create_repository_bulk_action(self, request, queryset):
        selected = list(queryset[:2])
        if len(selected) != 1:
            self.message_user(
                request,
                _("Select exactly one package to create a GitHub repository."),
                messages.WARNING,
            )
            return None

        package = selected[0]
        url = reverse("admin:release_package_create_repository", args=[package.pk])
        return redirect(url)

    @staticmethod
    def _slug_from_repository_url(repository_url: str) -> str:
        if not repository_url:
            return ""
        if repository_url.startswith("git@"):
            path_value = repository_url.partition(":")[2]
        else:
            parsed = urlparse(repository_url)
            path_value = parsed.path
        path_value = path_value.strip("/")
        if path_value.endswith(".git"):
            path_value = path_value[:-4]
        segments = [segment for segment in path_value.split("/") if segment]
        if len(segments) >= 2:
            return "/".join(segments[-2:])
        return ""

    def _repository_form_initial(self, package: Package) -> dict[str, object]:
        initial: dict[str, object] = {"description": package.description}
        slug = self._slug_from_repository_url(package.repository_url)
        if slug:
            initial["owner_repo"] = slug
        return initial

    def create_repository_view(self, request, object_id: int):
        package = get_object_or_404(Package, pk=object_id)

        if request.method == "POST":
            form = PackageRepositoryForm(request.POST)
            if form.is_valid():
                description = form.cleaned_data.get("description") or None
                try:
                    repository_url = create_repository_for_package(
                        package,
                        owner=form.cleaned_data["owner"],
                        repo=form.cleaned_data["repo"],
                        private=form.cleaned_data.get("private", False),
                        description=description,
                    )
                except GitHubRepositoryError as exc:
                    self.message_user(
                        request,
                        _("GitHub repository creation failed: %s") % exc,
                        messages.ERROR,
                    )
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.exception(
                        "Unexpected error while creating GitHub repository for %s",
                        package,
                    )
                    self.message_user(
                        request,
                        _("GitHub repository creation failed: %s") % exc,
                        messages.ERROR,
                    )
                else:
                    package.repository_url = repository_url
                    package.save(update_fields=["repository_url"])
                    self.message_user(
                        request,
                        _("GitHub repository created: %s") % repository_url,
                        messages.SUCCESS,
                    )
                    change_url = reverse(
                        "admin:release_package_change", args=[package.pk]
                    )
                    return redirect(change_url)
        else:
            form = PackageRepositoryForm(initial=self._repository_form_initial(package))

        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "original": package,
                "title": _("Create GitHub repository"),
                "form": form,
            }
        )
        return TemplateResponse(
            request, "admin/repos/package/create_repository.html", context
        )

@admin.register(PackageRelease)
class PackageReleaseAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_list_template = "admin/core/packagerelease/change_list.html"
    list_display = (
        "version",
        "package_link",
        "severity",
        "is_current",
        "pypi_url",
        "release_on",
        "revision_short",
        "published_status",
    )
    list_display_links = ("version",)
    actions = ["publish_release", "validate_releases", "test_pypi_connection"]
    change_actions = ["publish_release_action", "test_pypi_connection_action"]
    changelist_actions = ["refresh_from_pypi", "prepare_next_release"]
    readonly_fields = ("pypi_url", "github_url", "release_on", "is_current", "revision")
    search_fields = ("version", "package__name")
    fields = (
        "package",
        "release_manager",
        "version",
        "severity",
        "revision",
        "is_current",
        "pypi_url",
        "github_url",
        "scheduled_date",
        "scheduled_time",
        "release_on",
    )

    @admin.display(description="package", ordering="package")
    def package_link(self, obj):
        url = reverse("admin:release_package_change", args=[obj.package_id])
        return format_html('<a href="{}">{}</a>', url, obj.package)

    def revision_short(self, obj):
        return obj.revision_short

    revision_short.short_description = "revision"

    @admin.display(description="Scheduled for", ordering="scheduled_date")
    def scheduled_for(self, obj):
        moment = obj.scheduled_datetime
        if not moment:
            return "—"
        return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M")

    def refresh_from_pypi(self, request, queryset):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return
        resp = None
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{package.name}/json", timeout=10
            )
            resp.raise_for_status()
            releases = resp.json().get("releases", {})
        except Exception as exc:  # pragma: no cover - network failure
            self.message_user(request, str(exc), messages.ERROR)
            return
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
        updated = 0
        restored = 0
        missing: list[str] = []

        for version, files in releases.items():
            release_on = self._release_on_from_files(files)
            release = PackageRelease.all_objects.filter(
                package=package, version=version
            ).first()
            if release:
                update_fields = []
                if release.is_deleted:
                    release.is_deleted = False
                    update_fields.append("is_deleted")
                    restored += 1
                if not release.pypi_url:
                    release.pypi_url = (
                        f"https://pypi.org/project/{package.name}/{version}/"
                    )
                    update_fields.append("pypi_url")
                if release_on and release.release_on != release_on:
                    release.release_on = release_on
                    update_fields.append("release_on")
                    updated += 1
                if update_fields:
                    release.save(update_fields=update_fields)
                continue
            missing.append(version)

        if updated or restored:
            PackageRelease.dump_fixture()
            message_parts = []
            if updated:
                message_parts.append(
                    f"Updated release date for {updated} release"
                    f"{'s' if updated != 1 else ''}"
                )
            if restored:
                message_parts.append(
                    f"Restored {restored} release{'s' if restored != 1 else ''}"
                )
            self.message_user(request, "; ".join(message_parts), messages.SUCCESS)
        elif not missing:
            self.message_user(request, "No matching releases found", messages.INFO)

        if missing:
            versions = ", ".join(sorted(missing))
            count = len(missing)
            message = (
                "Manual creation required for "
                f"{count} release{'s' if count != 1 else ''}: {versions}"
            )
            self.message_user(request, message, messages.WARNING)

    refresh_from_pypi.label = "Refresh from PyPI"
    refresh_from_pypi.short_description = "Refresh from PyPI"

    @staticmethod
    def _release_on_from_files(files):
        if not files:
            return None
        candidates = []
        for item in files:
            stamp = item.get("upload_time_iso_8601") or item.get("upload_time")
            if not stamp:
                continue
            when = parse_datetime(stamp)
            if when is None:
                continue
            if timezone.is_naive(when):
                when = timezone.make_aware(when, datetime.timezone.utc)
            candidates.append(when.astimezone(datetime.timezone.utc))
        if not candidates:
            return None
        return min(candidates)

    def prepare_next_release(self, request, queryset):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return redirect("admin:release_packagerelease_changelist")
        return PackageAdmin._prepare(self, request, package)

    prepare_next_release.label = "Prepare next Release"
    prepare_next_release.short_description = "Prepare next release"

    def _publish_release(self, request, release):
        try:
            release.full_clean()
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), messages.ERROR)
            return
        return redirect(reverse("release-progress", args=[release.pk, "publish"]))

    @admin.action(description="Publish selected release(s)")
    def publish_release(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Select exactly one release to publish", messages.ERROR
            )
            return
        return self._publish_release(request, queryset.first())

    def publish_release_action(self, request, obj):
        return self._publish_release(request, obj)

    publish_release_action.label = "Publish selected Release"
    publish_release_action.short_description = "Publish this release"

    def _emit_pypi_check_messages(
        self, request, release, result: release_utils.PyPICheckResult
    ) -> None:
        level_map = {
            "success": messages.SUCCESS,
            "warning": messages.WARNING,
            "error": messages.ERROR,
        }
        prefix = f"{release}: "
        for level, message in result.messages:
            self.message_user(request, prefix + message, level_map.get(level, messages.INFO))
        if result.ok:
            self.message_user(
                request,
                f"{release}: PyPI connectivity check passed",
                messages.SUCCESS,
            )

    @admin.action(description="Test PyPI connectivity")
    def test_pypi_connection(self, request, queryset):
        if not queryset:
            self.message_user(
                request,
                "Select at least one release to test",
                messages.ERROR,
            )
            return
        for release in queryset:
            result = release_utils.check_pypi_readiness(release=release)
            self._emit_pypi_check_messages(request, release, result)

    def test_pypi_connection_action(self, request, obj):
        result = release_utils.check_pypi_readiness(release=obj)
        self._emit_pypi_check_messages(request, obj, result)

    test_pypi_connection_action.label = "Test PyPI connectivity"
    test_pypi_connection_action.short_description = "Test PyPI connectivity"

    @admin.action(description="Validate selected Releases")
    def validate_releases(self, request, queryset):
        deleted = False
        for release in queryset:
            if not release.pypi_url:
                self.message_user(
                    request,
                    f"{release} has not been published yet",
                    messages.WARNING,
                )
                continue
            url = f"https://pypi.org/pypi/{release.package.name}/{release.version}/json"
            resp = None
            try:
                resp = requests.get(url, timeout=10)
            except Exception as exc:  # pragma: no cover - network failure
                self.message_user(request, f"{release}: {exc}", messages.ERROR)
                continue

            try:
                if resp.status_code == 200:
                    continue
                release.delete()
                deleted = True
                self.message_user(
                    request,
                    f"Deleted {release} as it was not found on PyPI",
                    messages.WARNING,
                )
            finally:
                if resp is not None:
                    close = getattr(resp, "close", None)
                    if callable(close):
                        with contextlib.suppress(Exception):
                            close()
        if deleted:
            PackageRelease.dump_fixture()

    @staticmethod
    def _boolean_icon(value: bool) -> str:
        icon = static("admin/img/icon-yes.svg" if value else "admin/img/icon-no.svg")
        alt = "True" if value else "False"
        return format_html('<img src="{}" alt="{}">', icon, alt)

    @admin.display(description="Published")
    def published_status(self, obj):
        return self._boolean_icon(obj.is_published)

    @admin.display(description="Is current")
    def is_current(self, obj):
        return self._boolean_icon(obj.is_current)

