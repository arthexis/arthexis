from django.core.management.base import BaseCommand, CommandError

from ...models import PackageRelease
from ... import release as release_utils


class Command(BaseCommand):
    help = "Check PyPI connectivity and credentials for a package release."

    def add_arguments(self, parser):
        parser.add_argument(
            "release",
            nargs="?",
            help=(
                "Release primary key or version to check. "
                "Defaults to the latest release for the active package."
            ),
        )

    def handle(self, *args, **options):
        release_obj = self._resolve_release(options.get("release"))
        self.stdout.write(self.style.MIGRATE_HEADING(f"Checking {release_obj}"))
        result = release_utils.check_pypi_readiness(release=release_obj)
        level_styles = {
            "success": self.style.SUCCESS,
            "warning": self.style.WARNING,
            "error": self.style.ERROR,
        }
        for level, message in result.messages:
            style = level_styles.get(level, str)
            if level == "error":
                self.stderr.write(style(message))
            else:
                self.stdout.write(style(message))
        if result.ok:
            self.stdout.write(self.style.SUCCESS("PyPI connectivity check passed"))
            return
        self.stderr.write(self.style.ERROR("PyPI connectivity check failed"))
        raise CommandError("PyPI connectivity check failed")

    def _resolve_release(self, identifier):
        queryset = PackageRelease.objects.select_related(
            "package", "release_manager", "package__release_manager"
        )
        if identifier:
            try:
                return queryset.get(pk=int(identifier))
            except (ValueError, PackageRelease.DoesNotExist):
                active_match = queryset.filter(
                    package__is_active=True, version=identifier
                ).first()
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

