from __future__ import annotations

import contextlib
import datetime

import requests
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.templatetags.static import static
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EntityModelAdmin, SaveBeforeChangeAction
from apps.release import release as release_utils
from apps.release.admin.credentials import ReleaseManagerAdmin
from apps.release.admin.package_actions import (
    PackageAdminActionsMixin,
    prepare_package_release,
)
from apps.release.models import Package, PackageRelease, ReleaseManager


class PackageAdmin(PackageAdminActionsMixin, SaveBeforeChangeAction, EntityModelAdmin):
    actions = ["create_repository_bulk_action"]
    list_display = (
        "name",
        "description",
        "homepage_url",
        "release_manager",
        "is_active",
    )
    change_actions = ["create_repository_action", "prepare_next_release_action"]


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

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "refresh-from-pypi/",
                self.admin_site.admin_view(self.refresh_from_pypi_view),
                name="release_packagerelease_refresh_from_pypi",
            ),
        ]
        return custom + urls

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
        missing: list[tuple[str, datetime.datetime | None]] = []

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
            missing.append((version, release_on))

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
            new_releases = [
                PackageRelease(
                    package=package,
                    release_manager=package.release_manager,
                    version=version,
                    pypi_url=f"https://pypi.org/project/{package.name}/{version}/",
                    release_on=release_on,
                )
                for version, release_on in missing
            ]
            PackageRelease.objects.bulk_create(new_releases, ignore_conflicts=True)
            PackageRelease.dump_fixture()

            created_count = len(new_releases)
            versions = ", ".join(sorted(version for version, _ in missing))
            message = (
                f"Created {created_count} release{'s' if created_count != 1 else ''} "
                f"from PyPI: {versions}"
            )
            self.message_user(request, message, messages.SUCCESS)

    refresh_from_pypi.requires_queryset = False
    refresh_from_pypi.label = "Refresh from PyPI"
    refresh_from_pypi.short_description = "Refresh from PyPI"

    def refresh_from_pypi_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        response = self.refresh_from_pypi(request, queryset=None)
        if response:
            return response

        return redirect("admin:release_packagerelease_changelist")

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
        return prepare_package_release(self, request, package)

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


admin.site.register(ReleaseManager, ReleaseManagerAdmin)
admin.site.register(Package, PackageAdmin)
admin.site.register(PackageRelease, PackageReleaseAdmin)


__all__ = [
    "PackageAdmin",
    "PackageReleaseAdmin",
    "ReleaseManagerAdmin",
]
