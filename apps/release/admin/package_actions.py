from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from packaging.version import InvalidVersion, Version

from apps.release.models import Package, PackageRelease
from apps.repos.forms import PackageRepositoryForm
from apps.repos.task_utils import GitHubRepositoryError, create_repository_for_package

logger = logging.getLogger(__name__)


def _safe_version(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _latest_release(
    existing_releases: list[PackageRelease],
) -> tuple[Version, PackageRelease] | None:
    candidates: list[tuple[Version, PackageRelease]] = []
    for release in existing_releases:
        parsed = _safe_version(release.version)
        if parsed is None:
            continue
        candidates.append((parsed, release))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _fetch_latest_pypi_version(package_name: str) -> Version | None:
    resp = None
    try:
        resp = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=10)
        if not resp.ok:
            return None
        releases = resp.json().get("releases", {})
        candidates = [
            parsed
            for version in releases
            if (parsed := _safe_version(version)) is not None
        ]
        if not candidates:
            return None
        return max(candidates)
    except Exception:
        return None
    finally:
        if resp is not None:
            close = getattr(resp, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def prepare_package_release(admin_view, request, package):
    if request.method == "GET":
        context = admin_view.admin_site.each_context(request)
        context.update(
            {
                "opts": Package._meta,
                "original": package,
                "title": _("Prepare next release"),
                "cancel_url": request.META.get("HTTP_REFERER")
                or reverse("admin:index"),
            }
        )
        return TemplateResponse(
            request,
            "admin/release/prepare_next_release_confirm.html",
            context,
        )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST", "GET"])

    existing_releases = list(PackageRelease.all_objects.filter(package=package))
    pypi_latest_version = _fetch_latest_pypi_version(package.name)
    if existing_releases:
        latest_release_info = _latest_release(existing_releases)
        if latest_release_info is not None:
            latest_version, latest_release = latest_release_info
            if (
                not latest_release.is_published
                and (
                    pypi_latest_version is None
                    or latest_version >= pypi_latest_version
                )
            ):
                if latest_release.is_deleted:
                    latest_release.is_deleted = False
                    latest_release.save(update_fields=["is_deleted"])
                return redirect(
                    reverse(
                        "admin:release_packagerelease_change",
                        args=[latest_release.pk],
                    )
                )

    ver_file = Path("VERSION")
    if ver_file.exists():
        raw_version = ver_file.read_text().strip()
        repo_version_text = PackageRelease.normalize_version(raw_version) or "0.0.0"
        repo_version = _safe_version(repo_version_text) or Version("0.0.0")
    else:
        repo_version = Version("0.0.0")

    pypi_latest = pypi_latest_version or Version("0.0.0")
    pypi_plus_one = Version(PackageRelease._format_patch_with_epoch(pypi_latest))
    next_version = max(repo_version, pypi_plus_one)
    release, _created = PackageRelease.all_objects.update_or_create(
        package=package,
        version=str(next_version),
        defaults={
            "is_deleted": False,
        },
    )
    return redirect(reverse("admin:release_packagerelease_change", args=[release.pk]))


class PackageAdminActionsMixin:
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
            ),
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
        return prepare_package_release(self, request, package)

    def prepare_next_release_action(self, request, obj):
        return prepare_package_release(self, request, obj)

    prepare_next_release_action.label = _("Prepare Next")
    prepare_next_release_action.short_description = _("Prepare Next")

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


__all__ = [
    "PackageAdminActionsMixin",
    "prepare_package_release",
]
