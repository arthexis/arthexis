"""Reusable health checks for release workflows."""

from __future__ import annotations

from django.core.management.base import CommandError

from apps.release import release as release_utils
from apps.release.models import PackageRelease


def resolve_release(identifier):
    """Resolve a release object by primary key or version string."""

    queryset = PackageRelease.objects.select_related("package")
    if identifier:
        try:
            return queryset.get(pk=int(identifier))
        except (ValueError, PackageRelease.DoesNotExist):
            active_match = queryset.filter(package__is_active=True, version=identifier).first()
            if active_match:
                return active_match
            try:
                return queryset.get(version=identifier)
            except PackageRelease.DoesNotExist as exc:
                raise CommandError(f"Release '{identifier}' not found") from exc
    release = queryset.filter(package__is_active=True).order_by("-pk").first()
    if release:
        return release
    release = queryset.order_by("-pk").first()
    if release:
        return release
    raise CommandError("No releases available to check")


def run_check_pypi(*, stdout, stderr, style, release_identifier=None, **_kwargs) -> None:
    """Check PyPI connectivity and credentials for a package release."""

    release_obj = resolve_release(release_identifier)
    stdout.write(style.MIGRATE_HEADING(f"Checking {release_obj}"))
    result = release_utils.check_pypi_readiness(release=release_obj)
    level_styles = {
        "success": style.SUCCESS,
        "warning": style.WARNING,
        "error": style.ERROR,
    }
    for level, message in result.messages:
        style_fn = level_styles.get(level, str)
        if level == "error":
            stderr.write(style_fn(message))
        else:
            stdout.write(style_fn(message))
    if result.ok:
        stdout.write(style.SUCCESS("PyPI connectivity check passed"))
        return
    stderr.write(style.ERROR("PyPI connectivity check failed"))
    raise CommandError("PyPI connectivity check failed")
